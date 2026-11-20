"""Interface gráfica com CustomTkinter (Fase 1)."""

from spurgeon.ui import estabilidade, icone

estabilidade.instalar()  # `configure` que não muda nada não redesenha o widget (menos "piscar")
icone.instalar()  # as caixas (CTkToplevel) já nascem com o ícone do programa
