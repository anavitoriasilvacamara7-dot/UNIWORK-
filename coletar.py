import os
import json
import re
import socket
import time
from datetime import datetime, timezone, timedelta
import requests
import urllib3
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    {"nome": "FACAPE", "rede": "Pública", "url": "http://www.facape.br/"},
    {"nome": "UNEB", "rede": "Pública", "url": "https://portal.uneb.br/"},
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


def listar_modelos_flash(client):
    modelos = ["gemini-3.8-flash"]
    try:
        for m in client.models.list():
            nome = (getattr(m, "name", "") or "").replace("models/", "")
            acoes = getattr(m, "supported_actions", None) or []
            if acoes and "generateContent" not in acoes:
                continue
            nome_low = nome.lower()
            bloqueados = ("2.5", "embedding", "image", "audio", "tts", "live", "veo", "imagen", "aqa")
            if "gemini" in nome_low and "flash" in nome_low and not any(b in nome_low for b in bloqueados):
                if nome not in modelos:
                    modelos.append(nome)
    except Exception as e:
        print(f"Aviso ao listar modelos extras: {e}")
    return modelos[:4]


def filtrar_sem_ia(itens_brutos):
    hoje = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")
    palavras = {
        "Editais": ["edital", "seleção", "selecao", "monitoria", "estágio", "estagio", "inscrições", "inscricoes", "processo seletivo", "pibic", "extensão", "extensao"],
        "Bolsas": ["bolsa", "auxílio", "auxilio", "fomento", "pesquisa"],
        "Simpósios": ["simpósio", "simposio", "congresso", "semana", "jornada", "seminário", "seminario", "encontro", "fórum", "forum"],
        "Minicursos": ["minicurso", "oficina", "curso", "workshop", "capacitação", "capacitacao", "treinamento"],
        "Eventos": ["evento", "palestra", "hackathon", "mesa-redonda", "feira", "mostra", "aula inaugural"]
    }
    aprovados = []
    for idx, item in enumerate(itens_brutos, start=1):
        titulo_low = item["titulo_bruto"].lower()
        categoria = None
        for cat, termos in palavras.items():
            if any(t in titulo_low for t in termos):
                categoria = cat
                break
        if categoria or item["fonte"] == "Sympla Petrolina":
            slug = re.sub(r"[^a-z0-9]+", "-", titulo_low)[:40].strip("-") or "item"
            aprovados.append({
                "id": f"{slug}-{idx}",
                "titulo": item["titulo_bruto"],
                "instituicao": item["fonte"].replace(" Geral", "").replace("Sympla Petrolina", "Evento Local"),
                "rede": item["rede"],
                "categoria": categoria or "Eventos",
                "data": hoje,
                "prazo": "Consultar no link",
                "local": "Petrolina - PE",
                "beneficio": "Ver detalhes no site",
                "requisitos": "Estudantes universitários",
                "link": item["link"],
                "descricao": f"Oportunidade coletada automaticamente em {item['fonte']}.",
                "fonte": item["fonte"]
            })
    return aprovados[:25]


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

    modelos = listar_modelos_flash(client)
    print(f"Modelos candidatos: {modelos}")

    for modelo in modelos:
        for tentativa in range(1, 4):
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                texto = (response.text or "").strip()
                texto = re.sub(r"^```json\s*|\s*```$", "", texto, flags=re.MULTILINE).strip()
                if not texto:
                    continue
                dados = json.loads(texto)
                if isinstance(dados, dict):
                    for valor in dados.values():
                        if isinstance(valor, list):
                            dados = valor
                            break
                    else:
                        dados = []
                print(f"Modelo utilizado com sucesso: {modelo} (tentativa {tentativa})")
                return dados
            except Exception as e:
                erro_str = str(e)
                print(f"Aviso ({modelo}, tentativa {tentativa}): {erro_str}")
                if any(cod in erro_str for cod in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")):
                    espera = 10 * tentativa
                    print(f"Aguardando {espera}s para tentar novamente...")
                    time.sleep(espera)
                else:
                    break

    print("IA temporariamente indisponível; usando filtro local de contingência.")
    return filtrar_sem_ia(itens_brutos)


def main():
    brutos = coletar_links_brutos()
    print(f"Total de links brutos coletados: {len(brutos)}")
    itens = estruturar_com_ia(brutos)
    print(f"Itens aprovados: {len(itens)}")

    agora = datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")
    resultado = {
        "ultima_atualizacao": agora,
        "itens": itens
    }

    with open("dados.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
