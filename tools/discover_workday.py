"""Guess Workday tenants/sites and SuccessFactors (RMK) career hosts; report those with Vienna jobs.
usage: python tools/discover_workday.py out.json
"""
import json
import sys
import requests
from concurrent.futures import ThreadPoolExecutor
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64, max_retries=0))

WD_EXTRA = {
 "raiffeisen": "Raiffeisen", "rbinternational": "RBI", "erste": "Erste", "bankaustria": "Bank Austria", "kapsch": "Kapsch",
 "nttdata": "NTT Data", "endava": "Endava", "epam": "EPAM", "nagarro": "Nagarro", "globallogic": "GlobalLogic", "luxoft": "Luxoft",
 "trivago": "trivago", "sodexo": "Sodexo", "iss": "ISS", "manpower": "ManpowerGroup", "manpowergroup": "ManpowerGroup",
 "adecco": "Adecco", "randstad": "Randstad", "hays": "Hays", "pagegroup": "PageGroup", "robertwalters": "Robert Walters",
 "kornferry": "Korn Ferry", "egonzehnder": "Egon Zehnder", "spencerstuart": "Spencer Stuart", "publicis": "Publicis",
 "omnicom": "Omnicom", "wpp": "WPP", "groupm": "GroupM", "dentsu": "dentsu", "havas": "Havas", "ipsos": "Ipsos", "kantar": "Kantar",
 "gfk": "GfK", "nielsen": "Nielsen", "nielseniq": "NielsenIQ", "euromonitor": "Euromonitor", "statista": "Statista",
 "intrum": "Intrum", "coface": "Coface", "atradius": "Atradius", "ebrd": "EBRD", "worldbank": "World Bank", "unicef": "UNICEF",
 "booking": "Booking.com", "bookinghldgs": "Booking Holdings", "hilton": "Hilton", "marriott": "Marriott", "accor": "Accor",
 "mckesson": "McKesson", "cardinalhealth": "Cardinal Health", "iqvia": "IQVIA", "parexel": "Parexel", "icon": "ICON plc",
 "syneoshealth": "Syneos Health", "labcorp": "Labcorp", "fresenius": "Fresenius", "siemens-healthineers": "Siemens Healthineers",
 "teva": "Teva", "viatris": "Viatris", "stada": "STADA", "gedeonrichter": "Gedeon Richter", "servier": "Servier",
 "ipsen": "Ipsen", "ucb": "UCB", "lundbeck": "Lundbeck", "csl": "CSL", "grifols": "Grifols", "beigene": "BeiGene",
 "vertex": "Vertex", "regeneron": "Regeneron", "moderna": "Moderna", "biontech": "BioNTech", "illumina": "Illumina",
 "agilent": "Agilent", "waters": "Waters", "perkinelmer": "Revvity", "danaher": "Danaher", "beckmancoulter": "Beckman Coulter",
 "zimmerbiomet": "Zimmer Biomet", "smith-nephew": "Smith+Nephew", "edwards": "Edwards Lifesciences", "bostonscientific": "Boston Scientific",
 "olympus": "Olympus", "hologic": "Hologic", "bd": "BD", "cooper": "CooperCompanies", "alcon": "Alcon", "bausch": "Bausch + Lomb",
 "coty": "Coty", "esteelauder": "Estée Lauder", "puig": "Puig", "lvmh": "LVMH", "hugoboss": "HUGO BOSS", "adidas": "adidas",
 "levistrauss": "Levi Strauss", "tapestry": "Tapestry", "pvh": "PVH", "vfc": "VF Corp", "skechers": "Skechers", "nike": "Nike",
 "mattel": "Mattel", "hasbro": "Hasbro", "lego": "LEGO", "electrolux": "Electrolux", "whirlpool": "Whirlpool", "philipsdomestic": "Versuni",
 "logitech": "Logitech", "garmin": "Garmin", "jabra": "GN", "sonova": "Sonova", "dolby": "Dolby", "spotify": "Spotify",
 "warnerbros": "Warner Bros. Discovery", "disney": "Disney", "paramount": "Paramount", "universalmusic": "Universal Music",
 "sonymusic": "Sony Music", "warnermusic": "Warner Music", "bertelsmann": "Bertelsmann", "rtl": "RTL", "axelspringer": "Axel Springer",
}
WD = {  # tenant guesses -> display name
 "novartis": "Novartis", "sandoz": "Sandoz", "takeda": "Takeda", "pfizer": "Pfizer", "boehringer": "Boehringer Ingelheim",
 "boehringeringelheim": "Boehringer Ingelheim", "philips": "Philips", "unilever": "Unilever", "cchellenic": "Coca-Cola HBC",
 "coca-colahellenic": "Coca-Cola HBC", "cchbc": "Coca-Cola HBC", "pmi": "Philip Morris International", "jti": "JTI",
 "mmc": "Marsh McLennan / Oliver Wyman / Mercer", "aon": "Aon", "wtw": "WTW", "cisco": "Cisco", "dell": "Dell", "paypal": "PayPal",
 "visa": "Visa", "paysafe": "Paysafe", "bloomberg": "Bloomberg", "medtronic": "Medtronic", "thermofisher": "Thermo Fisher",
 "gsk": "GSK", "bristolmyerssquibb": "BMS", "amgen": "Amgen", "roche": "Roche", "bayer": "Bayer", "merckgroup": "Merck KGaA",
 "zeiss": "Zeiss", "kuehne-nagel": "Kuehne+Nagel", "kn": "Kuehne+Nagel", "dsv": "DSV", "adidas": "adidas / Runtastic",
 "swarovski": "Swarovski", "stryker": "Stryker", "abb": "ABB", "otis": "Otis", "kone": "KONE", "nxp": "NXP", "infineon": "Infineon",
 "hpe": "HPE", "adobe": "Adobe", "workday": "Workday", "autodesk": "Autodesk", "intel": "Intel", "nvidia": "NVIDIA",
 "lseg": "LSEG", "spglobal": "S&P Global", "moodys": "Moody's", "msci": "MSCI", "blackrock": "BlackRock", "statestreet": "State Street",
 "citi": "Citi", "db": "Deutsche Bank", "ubs": "UBS", "jpmc": "JPMorgan", "gs": "Goldman Sachs", "bnpparibas": "BNP Paribas",
 "socgen": "Société Générale", "ing": "ING", "allianz": "Allianz", "zurich": "Zurich", "generali": "Generali", "axa": "AXA",
 "kraftheinz": "Kraft Heinz", "pepsico": "PepsiCo", "danone": "Danone", "nestle": "Nestlé", "heineken": "Heineken",
 "carlsberg": "Carlsberg", "diageo": "Diageo", "pernod-ricard": "Pernod Ricard", "loreal": "L'Oréal", "beiersdorf": "Beiersdorf",
 "haleon": "Haleon", "reckitt": "Reckitt", "kenvue": "Kenvue", "abbott": "Abbott", "bd": "BD", "biogen": "Biogen",
 "lilly": "Eli Lilly", "novonordisk": "Novo Nordisk", "octapharma": "Octapharma", "siemens-energy": "Siemens Energy",
 "schneiderelectric": "Schneider Electric", "eaton": "Eaton", "honeywell": "Honeywell", "3m": "3M", "dow": "Dow",
 "basf": "BASF", "henkel": "Henkel", "lufthansa": "Lufthansa", "booking": "Booking", "expedia": "Expedia", "uber": "Uber",
 "bolt": "Bolt", "dhl": "DHL", "ups": "UPS", "fedex": "FedEx", "maersk": "Maersk", "ikea": "IKEA", "hm": "H&M",
 "puma": "PUMA", "nike": "Nike", "lvmh": "LVMH", "richemont": "Richemont", "kering": "Kering", "tiffany": "Tiffany",
 "sony": "Sony", "samsung": "Samsung", "canon": "Canon", "ricoh": "Ricoh", "xerox": "Xerox", "konicaminolta": "Konica Minolta",
 "atos": "Atos", "capgemini": "Capgemini", "cognizant": "Cognizant", "ntt": "NTT Data", "kyndryl": "Kyndryl", "dxc": "DXC",
 "accenture": "Accenture", "kpmg": "KPMG", "bdo": "BDO", "grantthornton": "Grant Thornton", "bain": "Bain", "bcg": "BCG",
 "simonkucher": "Simon-Kucher", "horvath": "Horváth", "bearingpoint": "BearingPoint", "alvarezandmarsal": "Alvarez & Marsal",
 "fticonsulting": "FTI Consulting", "kearney": "Kearney", "mckinsey": "McKinsey", "ey": "EY", "deloitte": "Deloitte",
 "uniqa": "UNIQA", "vig": "VIG", "erstegroup": "Erste Group", "bawag": "BAWAG", "unicredit": "UniCredit", "omv": "OMV",
 "verbund": "Verbund", "borealis": "Borealis", "wienerberger": "Wienerberger", "strabag": "Strabag", "a1": "A1 Telekom",
 "magenta": "Magenta", "redbull": "Red Bull", "rewe": "REWE", "spar": "SPAR", "mondelez": "Mondelez", "mars": "Mars",
}
SITES = ["External", "Careers", "careers", "External_Careers", "ExternalCareers", "ExternalCareerSite", "External_Career_Site",
         "{T}Careers", "{T}_Careers", "{T}", "{T}_External", "jobs", "Jobs", "Search", "SearchJobs", "en-US", "global",
         "Global", "campus", "Campus", "students", "University", "Early_Careers", "EarlyCareers", "experienced"]
WDN = [1, 3, 5, 103, 12, 102, 2, 10]

def wd_try(t):
    tenant, n, site = t
    site = site.replace("{T}", tenant.replace("-", "").capitalize())
    url = f"https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        r = S.post(url, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "Vienna"}, timeout=10,
                   headers={"Accept": "application/json", "Content-Type": "application/json"})
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    if "jobPostings" not in d:
        return None
    return {"kind": "workday", "tenant": tenant, "host": f"{tenant}.wd{n}.myworkdayjobs.com", "site": site,
            "total_vienna": d.get("total"), "titles": [j.get("title") for j in d["jobPostings"][:5]],
            "locs": [j.get("locationsText") for j in d["jobPostings"][:5]]}

def host_alive(t):
    tenant, n = t
    try:
        r = S.post(f"https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/x/jobs", json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=8, headers={"Accept": "application/json"})
        return (tenant, n, r.status_code)
    except requests.RequestException:
        return (tenant, n, None)

if len(sys.argv) > 2 and sys.argv[2] == "extra":
    WD = WD_EXTRA
out = []
# 1) which tenant/wdN hosts exist (unknown site answers 404/422 vs DNS/other failure)
with ThreadPoolExecutor(48) as ex:
    alive = [x for x in ex.map(host_alive, [(t, n) for t in WD for n in WDN]) if x[2] == 404]
print("alive hosts", len(alive), alive[:40], flush=True)
tasks = [(t, n, s) for t, n, _ in alive for s in SITES]
with ThreadPoolExecutor(32) as ex:
    for res in ex.map(wd_try, tasks):
        if res:
            res["company"] = WD.get(res["tenant"], res["tenant"]); out.append(res); print("WD", res, flush=True)
json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print("done", len(out))
