"""O cofre das chaves e dos acessos às redes.

Fica num arquivo próprio (`integracoes.json`, ao lado do `config.toml`), nunca dentro dele.
Os campos secretos (chaves de API, tokens) são criptografados pelo Windows (DPAPI): só o
mesmo usuário,
neste mesmo computador, consegue lê-los; copiar o arquivo para outro PC não revela nada.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

import platformdirs

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import NOME_APP
from spurgeon.publicacao.erros import ErroDePublicacao

VERSAO_DO_COFRE = 1
NOME_DO_ARQUIVO = "integracoes.json"
_ENTROPIA = b"StudioSpurgeon.integracoes.v1"

Protetor = Callable[[bytes], bytes]


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _como_blob(dados: bytes) -> tuple[_Blob, Any]:
    memoria = (ctypes.c_ubyte * len(dados)).from_buffer_copy(dados)
    return _Blob(len(dados), ctypes.cast(memoria, ctypes.POINTER(ctypes.c_ubyte))), memoria


def _dpapi(dados: bytes, *, proteger: bool) -> bytes:
    if sys.platform != "win32":  # pragma: no cover - o aplicativo é só para Windows
        raise ErroDePublicacao("O cofre de chaves só funciona no Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    entrada, _guarda_1 = _como_blob(dados)
    entropia, _guarda_2 = _como_blob(_ENTROPIA)
    saida = _Blob()
    if proteger:
        funcao = crypt32.CryptProtectData
        funcao.argtypes = [
            ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
        ]  # fmt: skip
        ok = funcao(ctypes.byref(entrada), "Studio Spurgeon", ctypes.byref(entropia), None, None, 0,
                    ctypes.byref(saida))  # fmt: skip
    else:
        funcao = crypt32.CryptUnprotectData
        funcao.argtypes = [
            ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
        ]  # fmt: skip
        ok = funcao(ctypes.byref(entrada), None, ctypes.byref(entropia), None, None, 0,
                    ctypes.byref(saida))  # fmt: skip
    if not ok:
        raise OSError(ctypes.FormatError(ctypes.get_last_error()))
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def proteger_com_o_windows(dados: bytes) -> bytes:
    return _dpapi(dados, proteger=True)


def revelar_com_o_windows(dados: bytes) -> bytes:
    return _dpapi(dados, proteger=False)


def caminho_do_cofre() -> Path:
    return platformdirs.user_config_path(NOME_APP, appauthor=False) / NOME_DO_ARQUIVO


class Cofre:
    """`conta` é a chave de uma integração (`youtube`, `meta`, `tiktok`…); `nome`, um campo dela."""

    def __init__(
        self,
        caminho: Path | None = None,
        *,
        proteger: Protetor = proteger_com_o_windows,
        revelar: Protetor = revelar_com_o_windows,
    ):
        self.caminho = Path(caminho) if caminho is not None else caminho_do_cofre()
        self._proteger = proteger
        self._revelar = revelar

    # --- leitura e escrita do arquivo -----------------------------------------------------------
    def _ler(self) -> dict[str, Any]:
        try:
            dados = ler_json(self.caminho)
        except (OSError, ValueError):
            return {"versao": VERSAO_DO_COFRE, "contas": {}}
        if not isinstance(dados, dict) or not isinstance(dados.get("contas"), dict):
            return {"versao": VERSAO_DO_COFRE, "contas": {}}
        return dados

    def _gravar(self, dados: dict[str, Any]) -> None:
        escrever_json_atomico(self.caminho, dados)

    # --- uso -------------------------------------------------------------------------------------
    def obter(self, conta: str, nome: str, padrao: str = "") -> str:
        """O valor do campo (descriptografado se for secreto). Segredo ilegível = `padrao`."""
        registro = self._ler()["contas"].get(conta, {})
        if nome in registro.get("campos", {}):
            return str(registro["campos"][nome])
        guardado = registro.get("segredos", {}).get(nome)
        if guardado is None:
            return padrao
        try:
            return self._revelar(base64.b64decode(guardado)).decode("utf-8")
        except (OSError, ValueError):
            return padrao  # outro usuário do Windows, outro PC ou arquivo corrompido

    def tem(self, conta: str, nome: str) -> bool:
        return bool(self.obter(conta, nome))

    def definir(self, conta: str, nome: str, valor: str, *, segredo: bool = False) -> None:
        """Guarda o campo; valor vazio apaga. Segredo vai criptografado, o resto em claro."""
        dados = self._ler()
        registro = dados["contas"].setdefault(conta, {"campos": {}, "segredos": {}})
        registro.setdefault("campos", {})
        registro.setdefault("segredos", {})
        registro["campos"].pop(nome, None)
        registro["segredos"].pop(nome, None)
        if valor:
            if segredo:
                bruto = self._proteger(valor.encode("utf-8"))
                registro["segredos"][nome] = base64.b64encode(bruto).decode("ascii")
            else:
                registro["campos"][nome] = valor
        if not registro["campos"] and not registro["segredos"]:
            dados["contas"].pop(conta, None)
        self._gravar(dados)

    def remover(self, conta: str, nome: str | None = None) -> None:
        """Apaga um campo da conta, ou a conta inteira (desconectar)."""
        dados = self._ler()
        if nome is None:
            dados["contas"].pop(conta, None)
        elif conta in dados["contas"]:
            dados["contas"][conta].get("campos", {}).pop(nome, None)
            dados["contas"][conta].get("segredos", {}).pop(nome, None)
        self._gravar(dados)

    def campos_da_conta(self, conta: str) -> list[str]:
        registro = self._ler()["contas"].get(conta, {})
        return [*registro.get("campos", {}), *registro.get("segredos", {})]

    def contas(self) -> list[str]:
        return list(self._ler()["contas"])
