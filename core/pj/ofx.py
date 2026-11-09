"""Leitor de extrato OFX (Open Financial Exchange).

Aceita as duas variantes que os bancos exportam:

- OFX 1.x (SGML): cabeçalho ``OFXHEADER:100`` e tags folha sem fechamento
  (``<TRNAMT>-50.00``);
- OFX 2.x (XML): as mesmas tags, com fechamento.

Não depende de biblioteca: o formato que interessa é pequeno (transações,
saldo e conta) e as bibliotecas existentes tropeçam justamente nas variações
dos bancos brasileiros -- vírgula decimal, data com fuso entre colchetes,
arquivo em Windows-1252.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

_BLOCO_TRN = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.S | re.I)
_FOLHA = re.compile(r"<([A-Z0-9.]+)>([^<\r\n]*)", re.I)
_BLOCO = {
    "saldo": re.compile(r"<LEDGERBAL>(.*?)</LEDGERBAL>", re.S | re.I),
    "conta": re.compile(r"<BANKACCTFROM>(.*?)</BANKACCTFROM>", re.S | re.I),
    "banco": re.compile(r"<FI>(.*?)</FI>", re.S | re.I),
}


@dataclass
class ExtratoLido:
    """O que qualquer leitor de extrato devolve, seja qual for o formato."""

    formato: str
    movimentos: list[dict]
    banco: str | None = None
    conta: str | None = None
    saldo_final: dict | None = None  # {"data": date, "valor": float}
    avisos: list[str] = field(default_factory=list)


def parece_ofx(conteudo: bytes) -> bool:
    cabeca = conteudo[:4096].upper()
    return b"OFXHEADER" in cabeca or b"<OFX>" in cabeca or b"<?OFX" in cabeca


def _decodificar(conteudo: bytes) -> str:
    cabeca = conteudo[:1024].upper()
    preferida = "cp1252" if b"CHARSET:1252" in cabeca or b"ENCODING:USASCII" in cabeca else "utf-8"
    for enc in (preferida, "utf-8", "cp1252", "latin-1"):
        try:
            return conteudo.decode(enc)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _folhas(trecho: str) -> dict[str, str]:
    return {k.upper(): v.strip() for k, v in _FOLHA.findall(trecho) if v.strip()}


def ler_valor(texto: str) -> float:
    """``-1.234,56``, ``-1234.56`` e ``1234,5`` viram float."""
    t = str(texto or "").strip().replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    return round(float(t), 2)


def ler_data(texto: str) -> date | None:
    """``20261105``, ``20261105120000`` e ``20261105120000[-3:BRT]``."""
    m = re.match(r"\s*(\d{4})(\d{2})(\d{2})", str(texto or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def chave_movimento(conta: str | None, mov: dict) -> str:
    """Identidade do movimento para não importar duas vezes.

    Com FITID (id do banco), ele manda: dois PIX iguais no mesmo dia são
    movimentos diferentes e têm FITIDs diferentes. Sem FITID, cai no hash de
    data, valor e descrição.
    """
    if mov.get("fitid"):
        base = f"ofx|{conta or ''}|{mov['fitid']}"
    else:
        base = f"h|{conta or ''}|{mov['data']}|{mov['valor']:.2f}|{mov['descricao']}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def ler_ofx(conteudo: bytes, nome_arquivo: str | None = None) -> ExtratoLido:
    texto = _decodificar(conteudo)
    avisos: list[str] = []

    # Um arquivo, uma conta. OFX com várias contas misturaria movimentos de
    # todas contra o saldo da primeira, e o mesmo FITID em contas diferentes
    # viraria a mesma chave (achado do Codex no PR #680).
    contas = {_folhas(b).get("ACCTID") for b in _BLOCO["conta"].findall(texto)}
    contas.discard(None)
    if len(contas) > 1:
        return ExtratoLido(formato="OFX", movimentos=[], avisos=[
            f"O arquivo traz {len(contas)} contas ({', '.join(sorted(contas))}). Exporte o "
            "extrato de uma conta por arquivo."])

    conta_folhas = _folhas(_BLOCO["conta"].search(texto).group(1)) if _BLOCO["conta"].search(texto) else {}
    conta = conta_folhas.get("ACCTID")
    banco_folhas = _folhas(_BLOCO["banco"].search(texto).group(1)) if _BLOCO["banco"].search(texto) else {}
    banco = banco_folhas.get("ORG") or conta_folhas.get("BANKID")

    movimentos: list[dict] = []
    descartados = 0
    for bloco in _BLOCO_TRN.findall(texto):
        f = _folhas(bloco)
        data = ler_data(f.get("DTPOSTED", ""))
        try:
            valor = ler_valor(f.get("TRNAMT", ""))
        except ValueError:
            valor = None
        if data is None or valor is None:
            descartados += 1
            continue
        nome = f.get("NAME", "")
        memo = f.get("MEMO", "")
        if nome and memo and memo.upper() != nome.upper() and nome.upper() not in memo.upper():
            descricao = f"{nome} — {memo}"
        else:
            descricao = memo or nome or f.get("TRNTYPE", "Movimento")
        mov = {
            "data": data,
            "valor": valor,
            "descricao": descricao[:300],
            "contraparte": (nome or memo)[:200],
            "tipo_banco": f.get("TRNTYPE"),
            "fitid": f.get("FITID"),
        }
        mov["chave"] = chave_movimento(conta, mov)
        movimentos.append(mov)
    if descartados:
        avisos.append(f"{descartados} transação(ões) sem data ou valor legível foram ignoradas.")

    saldo = None
    m = _BLOCO["saldo"].search(texto)
    if m:
        f = _folhas(m.group(1))
        try:
            saldo = {"data": ler_data(f.get("DTASOF", "")), "valor": ler_valor(f.get("BALAMT", ""))}
        except ValueError:
            saldo = None
        if saldo and saldo["data"] is None:
            saldo = None
    if not movimentos and not avisos:
        avisos.append("O arquivo OFX não trouxe nenhuma transação.")

    return ExtratoLido(formato="OFX", movimentos=movimentos, banco=banco, conta=conta,
                       saldo_final=saldo, avisos=avisos)
