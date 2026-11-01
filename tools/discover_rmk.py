"""Guess SAP SuccessFactors 'RMK' career site hosts for Austrian employers. usage: python tools/discover_rmk.py out.json"""
import json
import re
import sys
import requests
from concurrent.futures import ThreadPoolExecutor
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"
NAMES = {
 "erstegroup": "Erste Group", "erste": "Erste Group", "sparkasse": "Erste Bank & Sparkassen", "bawaggroup": "BAWAG", "bawag": "BAWAG",
 "bankaustria": "UniCredit Bank Austria", "unicreditgroup": "UniCredit", "omv": "OMV", "verbund": "Verbund", "a1": "A1 Telekom",
 "a1group": "A1 Group", "magenta": "Magenta Telekom", "uniqa": "UNIQA", "uniqagroup": "UNIQA", "vig": "VIG", "wienerstaedtische": "Wiener Städtische",
 "generali": "Generali", "allianz": "Allianz", "post": "Österreichische Post", "oebb": "ÖBB", "strabag": "Strabag", "porr": "Porr",
 "porr-group": "Porr", "wienerberger": "Wienerberger", "borealisgroup": "Borealis", "borealis": "Borealis", "agrana": "Agrana",
 "kapsch": "Kapsch", "novomatic": "Novomatic", "spar": "SPAR", "lenzing": "Lenzing", "andritz": "Andritz", "voestalpine": "voestalpine",
 "raiffeisen": "Raiffeisen", "rlbooe": "RLB OÖ", "oenb": "OeNB", "oekb": "OeKB", "wienerboerse": "Wiener Börse", "wienerstadtwerke": "Wiener Stadtwerke",
 "wienenergie": "Wien Energie", "viennaairport": "Flughafen Wien", "henkel": "Henkel", "swarovski": "Swarovski", "lidl": "Lidl",
 "hofer": "Hofer", "dm": "dm", "ikea": "IKEA", "siemens": "Siemens", "nestle": "Nestlé", "jti": "JTI", "jt-international": "JTI",
 "bat": "BAT", "danone": "Danone", "lufthansagroup": "Lufthansa Group", "austrian": "Austrian Airlines", "schenker": "DB Schenker",
 "dbschenker": "DB Schenker", "dhl": "DHL", "worldline": "Worldline", "atos": "Atos", "capgemini": "Capgemini", "kpmg": "KPMG",
 "bdo": "BDO", "tpa-group": "TPA", "grantthornton": "Grant Thornton", "hilti": "Hilti", "zumtobel": "Zumtobel", "infineon": "Infineon",
 "ams-osram": "ams OSRAM", "stada": "STADA", "takeda": "Takeda", "boehringer-ingelheim": "Boehringer Ingelheim", "merck": "Merck",
 "bayer": "Bayer", "novonordisk": "Novo Nordisk", "fresenius": "Fresenius", "vamed": "VAMED", "octapharma": "Octapharma",
 "coca-colahellenic": "Coca-Cola HBC", "cchellenic": "Coca-Cola HBC", "heineken": "Heineken", "brauunion": "Brau Union",
 "redbull": "Red Bull", "manner": "Manner", "rewe-group": "REWE Group", "billa": "BILLA", "mediamarkt": "MediaMarkt",
 "xxxlutz": "XXXLutz", "porsche": "Porsche Holding", "porscheholding": "Porsche Holding", "bmwgroup": "BMW", "magna": "Magna",
 "schindler": "Schindler", "kone": "KONE", "otis": "Otis", "abb": "ABB", "se": "Schneider Electric", "philips": "Philips",
 "canon": "Canon", "samsung": "Samsung", "huawei": "Huawei", "sap": "SAP", "ibm": "IBM", "oracle": "Oracle",
}
if len(sys.argv) > 2 and sys.argv[2] == "extra":
    NAMES = {
     "kapsch": "Kapsch", "frequentis": "Frequentis", "fabasoft": "Fabasoft", "raiffeisen-informatik": "Raiffeisen Informatik",
     "wienerstaedtische": "Wiener Städtische", "donauversicherung": "Donau Versicherung", "grawe": "GRAWE", "merkur": "Merkur",
     "volksbank": "Volksbank", "hyponoe": "HYPO NOE", "bank99": "bank99", "oenb": "OeNB", "fma": "FMA", "statistik": "Statistik Austria",
     "wienerlinien": "Wiener Linien", "wienernetze": "Wiener Netze", "wienerstadtwerke": "Wiener Stadtwerke", "wien-energie": "Wien Energie",
     "austrianairlines": "Austrian Airlines", "viennaairport": "Flughafen Wien", "flughafen-wien": "Flughafen Wien", "oebb": "ÖBB",
     "a1": "A1", "drei": "Drei", "magenta": "Magenta", "orf": "ORF", "prosiebensat1": "ProSiebenSat.1", "redbullmediahouse": "Red Bull Media House",
     "spar": "SPAR", "hofer": "HOFER", "aldi-sued": "ALDI SÜD", "lidl": "Lidl", "billa": "BILLA", "dm-drogerie": "dm", "xxxlgroup": "XXXLutz",
     "doka": "Doka", "umdasch": "Umdasch", "fronius": "Fronius", "agrana": "AGRANA", "manner": "Manner", "meinl": "Julius Meinl",
     "voeslauer": "Vöslauer", "rauch": "Rauch", "brauunion": "Brau Union", "heineken": "Heineken", "henkel": "Henkel", "beiersdorf": "Beiersdorf",
     "nestle": "Nestlé", "unilever": "Unilever", "loreal": "L'Oréal", "pg": "P&G", "jti": "JTI", "pmi": "PMI", "imperial": "Imperial Brands",
     "siemens": "Siemens", "siemens-energy": "Siemens Energy", "schneider-electric": "Schneider Electric", "abb": "ABB", "eaton": "Eaton",
     "honeywell": "Honeywell", "bosch": "Bosch", "continental": "Continental", "zf": "ZF", "magna": "Magna", "avl": "AVL", "miba": "Miba",
     "palfinger": "Palfinger", "rosenbauer": "Rosenbauer", "greiner": "Greiner", "semperit": "Semperit", "mm": "Mayr-Melnhof", "lenzing": "Lenzing",
     "boehringer-ingelheim": "Boehringer Ingelheim", "takeda": "Takeda", "novartis": "Novartis", "sandoz": "Sandoz", "roche": "Roche",
     "merckgroup": "Merck", "bayer": "Bayer", "gsk": "GSK", "sanofi": "Sanofi", "novonordisk": "Novo Nordisk", "fresenius": "Fresenius",
     "vamed": "VAMED", "stada": "STADA", "teva": "Teva", "octapharma": "Octapharma", "biomin": "Biomin", "dsm-firmenich": "dsm-firmenich",
     "kpmg": "KPMG", "bdo": "BDO", "grantthornton": "Grant Thornton", "tpa-group": "TPA", "deloitte": "Deloitte", "pwc": "PwC",
     "capgemini": "Capgemini", "atos": "Atos", "ibm": "IBM", "sap": "SAP", "oracle": "Oracle", "t-systems": "T-Systems",
     "dhl": "DHL", "dbschenker": "DB Schenker", "kuehne-nagel": "Kuehne+Nagel", "gw-world": "Gebrüder Weiss", "quehenberger": "Quehenberger",
     "tuwien": "TU Wien", "univie": "Universität Wien", "meduniwien": "MedUni Wien", "boku": "BOKU", "wu": "WU", "fh-campuswien": "FH Campus Wien",
     "technikum-wien": "FH Technikum Wien", "ait": "AIT", "ista": "ISTA", "oeaw": "ÖAW", "iiasa": "IIASA", "wifo": "WIFO", "ihs": "IHS",
    }
PATTERNS = ["jobs.{n}.com", "jobs.{n}.at", "careers.{n}.com", "karriere.{n}.at", "karriere.{n}.com", "career.{n}.com", "jobs.{n}.group",
            "jobs.{n}group.com", "careers.{n}group.com", "{n}.jobs", "jobs.{n}.eu", "careers.{n}.eu"]

def try_host(t):
    n, pat = t
    host = pat.format(n=n)
    for loc in ("Wien", "Vienna"):
        try:
            r = S.get(f"https://{host}/search/?q=&locationsearch={loc}&sortColumn=referencedate&sortDirection=desc", timeout=10)
        except requests.RequestException:
            return None
        if r.status_code == 200 and ("jobTitle-link" in r.text or "data-row" in r.text):
            rows = len(re.findall(r"jobTitle-link", r.text)) // 2 or len(re.findall(r'class="data-row', r.text))
            return {"host": host, "company": NAMES[n], "loc": loc, "rows": rows,
                    "titles": re.findall(r'class="jobTitle-link[^"]*"[^>]*>([^<]+)<', r.text)[:4]}
    return None

out = []
with ThreadPoolExecutor(32) as ex:
    for res in ex.map(try_host, [(n, p) for n in NAMES for p in PATTERNS]):
        if res:
            out.append(res); print("RMK", res, flush=True)
json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print("done", len(out))
