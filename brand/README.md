# O Verbo — Identidade Visual

Logo do aplicativo bíblico **O Verbo**.

## Conceito

O símbolo une dois versículos do Evangelho de João:

- **João 1:1** — *"No princípio era o Verbo"* → a letra **V** ("Verbo").
- **João 1:5** — *"A luz brilha nas trevas"* → o **V** desenhado como um **raio de luz dourado** que desce sobre uma **Bíblia aberta**.

A Palavra (luz) descende sobre as Escrituras (o livro), formando a inicial da marca.

## Paleta

| Cor | Hex | Uso |
|-----|-----|-----|
| Azul-noite | `#1B2A4A` | Fundo / tipografia |
| Azul profundo | `#0D1526` | Gradiente de fundo |
| Dourado claro | `#FBE08A` | Luz (topo do gradiente) |
| Dourado | `#E7B64B` | Cor de destaque principal |
| Âmbar | `#C98A2A` | Subtítulo / detalhes |
| Pergaminho | `#F7F3E8` | Páginas do livro |

Tipografia da marca: serifada (Georgia / Times) — tom clássico e reverente.

## Arquivos

| Arquivo | Descrição |
|---------|-----------|
| `o-verbo-icon.svg` / `.png` | Ícone do app (512×512, cantos arredondados) |
| `o-verbo-horizontal.svg` / `.png` | Logo horizontal (ícone + marca tipográfica) |
| `o-verbo-mono.svg` | Versão monocromática (1 cor, para carimbo/marca d'água) |

Os SVGs são vetoriais e podem ser escalados sem perda. Para gerar PNGs em
outras resoluções:

```bash
python3 -c "import cairosvg; cairosvg.svg2png(url='o-verbo-icon.svg', write_to='o-verbo-icon@1024.png', output_width=1024, output_height=1024)"
```
