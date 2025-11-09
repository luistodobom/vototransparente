Collecting workspace information# VotoTransparente PT 🇵🇹

**O Seu Guia para as Votações Parlamentares na Assembleia da República Portuguesa**

VotoTransparente é uma aplicação web que permite explorar e analisar as votações parlamentares da Assembleia da República Portuguesa. A aplicação extrai dados oficiais dos documentos parlamentares e utiliza Inteligência Artificial para processar e apresentar a informação de forma acessível.

## Funcionalidades

- 🔍 **Pesquisa de Propostas**: Procure por votações específicas usando palavras-chave
- 📜 **Navegação por Categorias**: Filtre propostas por categoria, partido proponente, resultado da votação e período de governo
- 🏛️ **Visualização Parlamentar**: Veja como cada partido votou com gráficos interativos
- 📊 **Análise Detalhada**: Resumos automáticos, análise crítica e impacto fiscal das propostas
- 🗣️ **Linguagem Simples**: Explicações em linguagem coloquial para facilitar a compreensão

## Como Usar

### 1. Obter os Dados

Para extrair os dados das votações parlamentares, execute:

```bash
python crawlers/pipeline.py
```

Este comando irá:
- Descarregar os PDFs das sessões parlamentares
- Extrair as propostas e votações usando LLM
- Fazer scraping dos detalhes das propostas
- Gerar resumos automáticos dos documentos
- Guardar tudo no ficheiro parliament_data.csv

### 2. Executar a Aplicação Web

Para lançar a interface web Streamlit, execute:

```bash
streamlit run streamlit_app/streamlit_app.py
```

A aplicação estará disponível em `http://localhost:8501`

## Estrutura do Projeto

- crawlers - Scripts para extração e processamento de dados
  - `pipeline.py` - Pipeline principal que orquestra todo o processo
- streamlit_app - Aplicação web Streamlit
  - `streamlit_app.py` - Página principal
  - `pages/` - Páginas adicionais da aplicação
- data - Dados extraídos e ficheiros de apoio

## Configuração

1. Crie um ficheiro .env na raiz do projeto com a sua chave da API Gemini:
   ```
   GEMINI_API_KEY=sua_chave_aqui
   ```

2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

## Tecnologias

- **Extração de Dados**: BeautifulSoup, Requests, PyPDF
- **Processamento com IA**: Google Gemini API
- **Interface Web**: Streamlit
- **Visualizações**: Matplotlib
- **Análise de Dados**: Pandas

## Contribuições

Este projeto foi desenvolvido com ❤️ por Luis Berenguer Todo-Bom. Os dados são extraídos de documentos oficiais da Assembleia da República e processados com Inteligência Artificial.

⚠️ **Nota**: A informação pode conter erros. Para reportar problemas, visite o [GitHub Issues](https://github.com/luistodobom/vototransparente/issues). 
