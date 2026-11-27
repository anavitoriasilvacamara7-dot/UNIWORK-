import os
import json
import re
import socket
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
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
    # Portais acadêmicos do Vale do São Francisco
    {"nome": "UNIVASF", "rede": "Pública", "foco": "academico", "url": "https://portais.univasf.edu.br/noticias"},
    {"nome": "UNIVASF Portal", "rede": "Pública", "foco": "academico", "url": "https://portais.univasf.edu.br/"},
    {"nome": "IF Sertão-PE", "rede": "Pública", "foco": "academico", "url": "https://www.ifsertao-pe.edu.br/"},
    {"nome": "UPE Petrolina", "rede": "Pública", "foco": "academico", "url": "https://www.upe.br/petrolina/"},
    {"nome": "UPE Geral", "rede": "Pública", "foco": "academico", "url": "https://www.upe.br/"},
    {"nome": "FACAPE", "rede": "Pública", "foco": "academico", "url": "http://www.facape.br/"},
    {"nome": "UNEB", "rede": "Pública", "foco": "academico", "url": "https://portal.uneb.br/"},
    {"nome": "Sympla Petrolina", "rede": "Privada", "foco": "academico", "url": "https://www.sympla.com.br/eventos/petrolina-pe"},

    # Portais de inovação, fomento e ideias inovadoras
    {"nome": "FACEPE", "rede": "Pública", "foco": "inovacao", "url": "https://www.facepe.br/editais/"},
    {"nome": "Finep", "rede": "Pública", "foco": "inovacao", "url": "http://www.finep.gov.br/chamadas-publicas"},
    {"nome": "Programa Centelha", "rede": "Pública", "foco": "inovacao", "url": "https://programacentelha.com.br/"},
    {"nome": "Sebrae Startups", "rede": "Privada", "foco": "inovacao", "url": "https://sebraestartups.com.br/"},
    {"nome": "Sebrae PE", "rede": "Privada", "foco": "inovacao", "url": "https://sebrae.com.br/sites/PortalSebrae/ufs/pe"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def extrair_texto_link(tag_a):
    texto = " ".join(tag_a.get_text().split())
    if len(texto) >= 14:
        return texto
    titulo = (tag_a.get("title") or "").strip()
    if len(titulo) >= 14:
        return titulo
    img = tag_a.find("img", alt=True)
    if img:
        alt = " ".join((img.get("alt") or "").split())
        if len(alt) >= 14:
            return alt
    return texto


def coletar_links_brutos():
    por_fonte = []
    for fonte in FONTES:
        itens_fonte = []
        try:
            resp = requests.get(fonte["url"], headers=HEADERS, timeout=20, verify=False)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # Remove menus de navegação e rodapés para priorizar conteúdo real
            for tag in soup.find_all(["nav", "footer", "script", "style", "noscript"]):
                tag.decompose()

            vistos = set()
            for a in soup.find_all("a", href=True):
                texto = extrair_texto_link(a)
                if len(texto) < 14 or len(texto) > 200:
                    continue

                href = urljoin(fonte["url"], a["href"].strip())
                if not href.startswith("http") or href in vistos:
                    continue
                if any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js")):
                    continue

                vistos.add(href)
                itens_fonte.append({
                    "fonte": fonte["nome"],
                    "rede": fonte["rede"],
                    "foco": fonte["foco"],
                    "titulo_bruto": texto,
                    "link": href
                })
                if len(itens_fonte) >= 20:
                    break

            print(f"{fonte['nome']}: {len(itens_fonte)} links coletados")
        except Exception as e:
            print(f"Aviso: não foi possível ler {fonte['nome']}: {e}")

        if itens_fonte:
            por_fonte.append(itens_fonte)

    # Intercala links de todas as fontes para garantir espaço às oportunidades de inovação
    coletados = []
    max_rodadas = max((len(lst) for lst in por_fonte), default=0)
    for idx in range(max_rodadas):
        for lst in por_fonte:
            if idx < len(lst):
                coletados.append(lst[idx])
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
        "Inovação": [
            "inovação", "inovacao", "startup", "empreendedorismo", "centelha",
            "aceleração", "aceleracao", "incubação", "incubacao", "hackathon",
            "pitch", "subvenção", "subvencao", "fomento", "ideação", "ideacao",
            "ideias inovadoras", "catalisa", "liga jovem", "propriedade intelectual",
            "chamada pública", "chamada publica", "inovativa", "sebrae"
        ],
        "Editais": ["edital", "seleção", "selecao", "monitoria", "estágio", "estagio", "inscrições", "inscricoes", "processo seletivo", "pibic", "extensão", "extensao"],
        "Bolsas": ["bolsa", "auxílio", "auxilio", "pesquisa", "residência", "residencia"],
        "Simpósios": ["simpósio", "simposio", "congresso", "semana", "jornada", "seminário", "seminario", "encontro", "fórum", "forum", "scientex"],
        "Minicursos": ["minicurso", "oficina", "curso", "workshop", "capacitação", "capacitacao", "treinamento"],
        "Eventos": ["evento", "palestra", "mesa-redonda", "feira", "mostra", "aula inaugural"]
    }
    aprovados = []
    for idx, item in enumerate(itens_brutos, start=1):
        titulo_low = item["titulo_bruto"].lower()
        categoria = None
        for cat, termos in palavras.items():
            if any(t in titulo_low for t in termos):
                categoria = cat
                break
        if not categoria and item.get("foco") == "inovacao":
            categoria = "Inovação"
        if categoria or item["fonte"] == "Sympla Petrolina":
            slug = re.sub(r"[^a-z0-9]+", "-", titulo_low)[:40].strip("-") or "item"
            local_padrao = "Pernambuco / Online" if item.get("foco") == "inovacao" else "Petrolina - PE"
            aprovados.append({
                "id": f"{slug}-{idx}",
                "titulo": item["titulo_bruto"],
                "instituicao": item["fonte"].replace(" Geral", "").replace(" Portal", "").replace("Sympla Petrolina", "Evento Local"),
                "rede": item["rede"],
                "categoria": categoria or "Eventos",
                "data": hoje,
                "prazo": "Consultar no portal",
                "local": local_padrao,
                "beneficio": "Fomento / Apoio" if categoria == "Inovação" else "Ver detalhes no site",
                "requisitos": "Estudantes e empreendedores" if categoria == "Inovação" else "Estudantes universitários",
                "link": item["link"],
                "descricao": f"Oportunidade coletada automaticamente em {item['fonte']}.",
                "fonte": item["fonte"]
            })
    return aprovados[:30]


def estruturar_com_ia(itens_brutos):
    if not itens_brutos:
        return []

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
    hoje = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")

    prompt = f"""
Você é o curador do aplicativo UniWork (iniciativa da liga acadêmica INOVA ADM), focado em estudantes universitários de Petrolina-PE, Juazeiro-BA e Pernambuco.
Hoje é {hoje}.

Analise a lista bruta de links coletados abaixo e FILTRE APENAS o que for realmente útil para estudantes e jovens empreendedores:
- Inovação: editais de fomento para ideias inovadoras, subvenção econômica, programas de pré-aceleração, aceleração, incubação, desafios de startups, hackathons, competições de pitch, Programa Centelha, Catalisa, chamadas da FACEPE, Finep, Sebrae Startups e editais de inovação universitária.
- Editais: monitoria, PIBIC, extensão, estágios, processos seletivos, residência pedagógica.
- Bolsas: bolsas de pesquisa, extensão, auxílio estudantil ou fomento individual.
- Simpósios: congressos, semanas acadêmicas, Scientex, jornadas, seminários e fóruns.
- Eventos: palestras, feiras, mesas-redondas e encontros universitários.
- Minicursos: oficinas, workshops e capacitações.

REGRAS IMPORTANTES:
1. Priorize fortemente oportunidades classificadas como "Inovação" quando houver links de fomento, startups, desafios, hackathons, FACEPE, Finep, Centelha ou Sebrae.
2. DESCARTE notícias genéricas, reuniões administrativas, notas de pesar, política partidária ou links institucionais vazios (como "Quem somos", "Contato", "Fale conosco").

Retorne EXCLUSIVAMENTE um JSON válido (uma lista de objetos), sem texto antes ou depois, seguindo exatamente este formato:
[
  {{
    "id": "slug-unico-curto",
    "titulo": "Título claro e atrativo",
    "instituicao": "UNIVASF | UPE Petrolina | IF Sertão-PE | FACAPE | UNEB | FACEPE | Finep | Sebrae | Programa Centelha | Evento Local",
    "rede": "Pública | Privada",
    "categoria": "Inovação | Editais | Simpósios | Eventos | Minicursos | Bolsas",
    "data": "YYYY-MM-DD (use a data do evento/prazo se inferível, senão {hoje})",
    "prazo": "Ex: Inscrições abertas, Até DD/MM ou Consulte o edital",
    "local": "Ex: Petrolina - PE, Pernambuco ou Online",
    "beneficio": "Ex: Fomento, Mentoria, Certificado, Bolsa ou Gratuito",
    "requisitos": "Resumo curto de quem pode participar",
    "link": "URL exata fornecida na entrada",
    "descricao": "Resumo objetivo em 1 ou 2 frases.",
    "fonte": "Nome da fonte"
  }}
]

Dados brutos:
{json.dumps(itens_brutos[:90], ensure_ascii=False)}
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
