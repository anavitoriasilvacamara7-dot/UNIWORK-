import os
import json
import re
import socket
from datetime import datetime, timezone, timedelta
import requests
import urllib3
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

# Desativa avisos de certificado SSL comum em sites universitários brasileiros
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Força conexão IPv4 para evitar erro "Network is unreachable" no GitHub Actions
try:
    import urllib3.util.connection as urllib3_cn
    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

FONTES = [
    {"nome": "UNIVASF", "rede": "Pública", "url": "https://portais.univasf.edu.br/noticias"},
    {"nome": "IF Sertão-PE", "rede": "Pública", "url": "https://www.ifsertao-pe.edu.br/"},
    {"nome": "UPE Petrolina", "rede": "Pública", "url": "https://www.upe.br/petrolina/"},
    {"nome": "UPE Geral", "rede": "Pública", "url": "https://www.upe.br/"},
    {"nome": "FACAPE", "rede": "Pública", "url": "https://www.facape.br/"},
    {"nome": "UNEB", "rede": "Pública", "url": "https://portal.uneb.br/noticias/"},
    {"nome": "Sympla Petrolina", "rede": "Privada", "url": "https://www.sympla.com.br/eventos/petrolina-pe"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def coletar_links_brutos():
    coletados = []
    for fonte in FONTES:
        try:
            resp = requests.get(fonte["url"], headers=HEADERS, timeout=20, verify=False)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            vistos = set()
            for a in soup.find_all("a", href=True):
                texto = " ".join(a.get_text().split())
                href = a["href"]
                if len(texto) < 25 or len(texto) > 180:
                    continue
                if href.startswith("/"):
                    base = "/".join(fonte["url"].split("/")[:3])
                    href = base + href
                if not href.startswith("http") or href in vistos:
                    continue
                vistos.add(href)
                coletados.append({
                    "fonte": fonte["nome"],
                    "rede": fonte["rede"],
                    "titulo_bruto": texto,
                    "link": href
                })
                if len(vistos) >= 15:
                    break
            print(f"{fonte['nome']}: {len(vistos)} links coletados")
        except Exception as e:
            print(f"Aviso: não foi possível ler {fonte['nome']}: {e}")
    return coletados

def estruturar_com_ia(itens_brutos):
    if not itens_brutos:
        return []

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    hoje = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")

    prompt = f"""
Você é o curador do aplicativo UniWork, focado em estudantes universitários de Petrolina-PE e Juazeiro-BA.
Hoje é {hoje}.

Analise a lista bruta de links coletados abaixo e FILTRE APENAS o que for útil para estudantes:
- Editais (monitoria, PIBIC, extensão, bolsas, estágios, seleção, auxílio)
- Simpósios, congressos, jornadas, semanas acadêmicas
- Eventos universitários, palestras, hackathons
- Minicursos e oficinas

DESCARTE notícias genéricas, reuniões de reitoria, notas de pesar, avisos administrativos ou links institucionais vazios.

Retorne EXCLUSIVAMENTE um JSON válido (uma lista de objetos), sem texto antes ou depois, seguindo exatamente este formato:
[
  {{
    "id": "slug-unico-curto",
    "titulo": "Título claro e atrativo",
    "instituicao": "UNIVASF | UPE Petrolina | IF Sertão-PE | FACAPE | UNINASSAU | Estácio | UNEB",
    "rede": "Pública | Privada",
    "categoria": "Editais | Simpósios | Eventos | Minicursos | Bolsas",
    "data": "YYYY-MM-DD (use a data do evento/prazo se inferível, senão {hoje})",
    "prazo": "Ex: Inscrições abertas ou Até DD/MM",
    "local": "Ex: Petrolina - PE ou Online",
    "beneficio": "Ex: Certificado, Bolsa, Gratuito ou Acesso livre",
    "requisitos": "Resumo curto de quem pode participar",
    "link": "URL exata fornecida na entrada",
    "descricao": "Resumo objetivo em 1 ou 2 frases.",
    "fonte": "Nome da fonte"
  }}
]

Dados brutos:
{json.dumps(itens_brutos[:70], ensure_ascii=False)}
"""

    modelos = ["gemini-3.8-flash", "gemini-3.0-flash"]
    response = None
    ultimo_erro = None

    for modelo in modelos:
        try:
            response = client.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            print(f"Modelo utilizado com sucesso: {modelo}")
            break
        except Exception as e:
            print(f"Aviso ao tentar modelo {modelo}: {e}")
            ultimo_erro = e

    if response is None:
        raise ultimo_erro

    texto = (response.text or "").strip()
    texto = re.sub(r"^```json\s*|\s*```$", "", texto, flags=re.MULTILINE).strip()
    if not texto:
        return []

    dados = json.loads(texto)
    if isinstance(dados, dict):
        for valor in dados.values():
            if isinstance(valor, list):
                return valor
        return []
    return dados

def main():
    brutos = coletar_links_brutos()
    print(f"Total de links brutos coletados: {len(brutos)}")
    itens = estruturar_com_ia(brutos)
    print(f"Itens aprovados pela IA: {len(itens)}")

    agora = datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")
    resultado = {
        "ultima_atualizacao": agora,
        "itens": itens
    }

    with open("dados.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
