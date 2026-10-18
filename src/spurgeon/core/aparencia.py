"""O "estilo" da imagem do vídeo: o enquadramento (zoom) e o tratamento de cor.

São modelos pequenos, sem FFmpeg nem interface, que valem para o vídeo longo e para os Shorts. A
`OpcoesProcessamento` guarda os mesmos valores em campos soltos (para a assinatura de cada etapa e
para o `estado.json`); estes modelos são o que se salva como **preset** no aplicativo.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

ZOOM_MAXIMO_PCT = 300.0
NEUTRO = 1e-6


def _par(valor: float) -> int:
    """O inteiro par mais perto (o H.264 em 4:2:0 só aceita medidas pares)."""
    return max(2, round(valor / 2) * 2)


class Enquadramento(BaseModel):
    """Aproxima a câmera: um recorte do quadro (zoom em %) ampliado de volta ao tamanho original.

    `centro_*_pct` é o ponto do quadro (em % da largura e da altura) onde o recorte se centra; o
    recorte nunca sai do quadro, então perto das bordas ele para de andar.
    """

    zoom_pct: float = Field(100.0, ge=100.0, le=ZOOM_MAXIMO_PCT)
    centro_x_pct: float = Field(50.0, ge=0.0, le=100.0)
    centro_y_pct: float = Field(50.0, ge=0.0, le=100.0)

    @property
    def ativo(self) -> bool:
        return self.zoom_pct > 100.0 + NEUTRO

    def regiao(self, largura: int, altura: int) -> tuple[int, int, int, int]:
        """(x, y, largura, altura) do recorte, em pixels pares, dentro de `largura`×`altura`."""
        if not self.ativo:
            return 0, 0, largura // 2 * 2, altura // 2 * 2
        fator = self.zoom_pct / 100.0
        w = min(_par(largura / fator), largura // 2 * 2)
        h = min(_par(altura / fator), altura // 2 * 2)
        x = min(max(largura * self.centro_x_pct / 100.0 - w / 2, 0), largura - w)
        y = min(max(altura * self.centro_y_pct / 100.0 - h / 2, 0), altura - h)
        return int(x) // 2 * 2, int(y) // 2 * 2, w, h

    def centro_valido(self, largura: int, altura: int) -> tuple[float, float]:
        """O centro (em %) que o recorte realmente tem, depois de impedir que saia do quadro."""
        x, y, w, h = self.regiao(largura, altura)
        return (x + w / 2) / largura * 100.0, (y + h / 2) / altura * 100.0


class TratamentoImagem(BaseModel):
    """Ajustes básicos de cor e luz. Cada valor vai de −100 a +100; 0 não muda nada."""

    brilho: float = Field(0.0, ge=-100.0, le=100.0)
    contraste: float = Field(0.0, ge=-100.0, le=100.0)
    saturacao: float = Field(0.0, ge=-100.0, le=100.0)
    #: correção de cor: − esfria a imagem (azulada), + esquenta (amarelada)
    temperatura: float = Field(0.0, ge=-100.0, le=100.0)
    #: correção de cor: − puxa para o verde, + para o magenta
    matiz: float = Field(0.0, ge=-100.0, le=100.0)
    #: − recupera os brancos estourados, + clareia os brancos
    brancos: float = Field(0.0, ge=-100.0, le=100.0)
    #: − aprofunda as sombras, + abre (clareia) as sombras
    sombras: float = Field(0.0, ge=-100.0, le=100.0)

    @property
    def neutro(self) -> bool:
        return all(abs(v) < NEUTRO for v in self.model_dump().values())


#: nome mostrado na interface de cada ajuste do tratamento, na ordem da tela
AJUSTES_DO_TRATAMENTO: dict[str, tuple[str, str]] = {
    "brilho": ("Brilho", "Clareia ou escurece a imagem toda."),
    "contraste": ("Contraste", "Afasta os claros dos escuros: mais “pegada”, ou imagem mais lisa."),
    "saturacao": ("Saturação", "Intensidade das cores; no mínimo a imagem fica em preto e branco."),
    "temperatura": ("Temperatura", "Corrige a cor: esquerda esfria (azul), direita esquenta."),
    "matiz": ("Tom (verde ↔ magenta)", "Tira ou acrescenta o verde que algumas luzes põem."),
    "brancos": ("Brancos", "Recupera os brancos estourados (esquerda) ou os clareia (direita)."),
    "sombras": ("Sombras", "Aprofunda as áreas escuras (esquerda) ou as abre (direita)."),
}  # fmt: skip
