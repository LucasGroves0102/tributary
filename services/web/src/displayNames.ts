/** Names as people read them. SEC conformed names are upper case with legal suffixes and state
 * tags ("COSTCO WHOLESALE CORP /NEW", "WELLS FARGO & COMPANY/MN", "TOYOTA MOTOR CORP/"). This is
 * display formatting only: the stored name is untouched and CSV exports keep it.
 *
 * In order: state tags and legal suffixes come off, then a table of brands the rules can't get,
 * then the SEC's surname-first forms ("HUNT J B TRANSPORT" is J.B. Hunt Transport), then casing,
 * which only touches a name with no lower case at all: one a filer wrote properly ("lululemon
 * athletica inc.") keeps its own capitals. */

/** What a company reads as while its name is unknown: never its id (companies/directory.ts). */
export const UNNAMED = "Company";

/** Whole names only: the legal suffix was what made these a name at all, or the SEC's shorthand. */
const EXACT: Record<string, string> = {
  NEWS: "News Corp",
  SOUTHERN: "Southern Company",
  "US BANCORP DE": "U.S. Bancorp",
  "V F": "VF",
  "FEDERAL NATIONAL MORTGAGE ASSOCIATION FANNIE MAE": "Fannie Mae",
  "PETROBRAS - PETROLEO BRASILEIRO": "Petrobras",
};

/** Whole names or the start of one: brands with a capital inside, and the SEC's spellings. */
const BRANDS: Record<string, string> = {
  "COCA COLA": "Coca-Cola", JPMORGAN: "JPMorgan", PEPSICO: "PepsiCo", MCDONALDS: "McDonald's",
  "MCDONALD'S": "McDonald's", NVIDIA: "Nvidia", UNITEDHEALTH: "UnitedHealth", "AMAZON COM": "Amazon",
  "EXXON MOBIL": "Exxon Mobil", "KEURIG DR PEPPER": "Keurig Dr Pepper",
  FEDEX: "FedEx", KEYCORP: "KeyCorp", METLIFE: "MetLife", EBAY: "eBay", CARMAX: "CarMax",
  CONOCOPHILLIPS: "ConocoPhillips", MERCADOLIBRE: "MercadoLibre", DAVITA: "DaVita", BORGWARNER: "BorgWarner",
  AUTONATION: "AutoNation", AUTOZONE: "AutoZone", FIRSTENERGY: "FirstEnergy", CENTERPOINT: "CenterPoint",
  NEXTERA: "NextEra", NISOURCE: "NiSource", PULTEGROUP: "PulteGroup", JETBLUE: "JetBlue", MASTEC: "MasTec",
  RENAISSANCERE: "RenaissanceRe", L3HARRIS: "L3Harris", ONEOK: "ONEOK", PACCAR: "PACCAR", WESCO: "WESCO",
  GLOBALFOUNDRIES: "GlobalFoundries", "FREEPORT-MCMORAN": "Freeport-McMoRan",
  "COLGATE PALMOLIVE": "Colgate-Palmolive", "SHERWIN WILLIAMS": "Sherwin-Williams",
  "KIMBERLY CLARK": "Kimberly-Clark", "BRISTOL MYERS SQUIBB": "Bristol-Myers Squibb",
  "CURTISS WRIGHT": "Curtiss-Wright", LOWES: "Lowe's", KOHLS: "Kohl's", CASEYS: "Casey's",
  "PILGRIMS PRIDE": "Pilgrim's Pride", "O REILLY": "O'Reilly",
  "SCHWAB CHARLES": "Charles Schwab", "PRICE T ROWE": "T. Rowe Price", "US BANCORP": "U.S. Bancorp",
  "R F": "RF", "G III": "G-III",
};

/** " /DE/", "/NEW", "/ADR/", " / MA", "\DE\", " (DE)": a state or listing tag, not part of the name. */
const TAG = /\s*(?:[/\\]\s*(?:[A-Z]{2,6}|Cayman)\s*[/\\]?|\([A-Z]{2,3}\))\s*$/;
/** "LIFECORE BIOMEDICAL, INC. DE": the same tag, bare, after a legal suffix. */
const BARE_TAG = /(\b(?:INC|CORP|LTD|CO)\.?),?\s+[A-Z]{2}$/;
const SUFFIX =
  /[\s,]+(PUBLIC\s+(?:LTD|LIMITED|CO(?:MPANY)?)(?:\s+(?:LTD|LIMITED|CO(?:MPANY)?))?|&\s*CO(?:MPANY)?|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|L\.?P|N\.?\s?V|S\.?\s?A|S\.?\s?P\.?\s?A|S\.?A\.?B(?:\s+DE\s+C\.?V)?|A\/S|ASA|AB|AG|SE|OYJ|TBK)\.?$/i;

/** Short words in an all-capitals name that are words, not initials: anything else of three
 * letters or fewer stays capitals (HNI, AAR, KLA, EOG, AES, WEC). */
const SHORT_WORDS = new Set(
  ("OF AND THE FOR IN ON AT TO BY AN AS OR NEW OLD ONE TWO SIX TEN AIR OIL GAS BIO BAY BIG RED SKY SUN SEA ALL " +
    "CAN CAP CAR DAY DOG DOT END FAR FIX FLY FOX GAP GEO GET HOT HUB ICE INN JET JOE KEY KID LAB LAM LAW LEE LOW MAN MAX MET MID NET NOW " +
    "NUT OAK ORE OUR OWL PAD PAN PAR PAY PET PIE PIN POP PRO RAY RIO ROW RUN SAM SAN LAS LOS DEL DE LA EL SOL SON TAX TEA TOP TOY VAN " +
    "VIA WAY WEB WIN ZOO ACE AGE AID ARC ARK ART ASH BAR BEE BOX BUY COM DAN DOW EGG ERA EYE FAN FAT FEE FIT GEM GO HAT ION IVY JOY KEN " +
    "LED LOG LUX MAP MY NO NOR ODD PEN PIG PUB RAM RAW ROD RUM UP ELI ROY LEO IAN KIM TED RON JIM BOB BEN AMY ANN SUE LOU DON GUY JAY " +
    "PAT VIC WES YUM MED ECO BOY BOW TAP TEK GRO CO INC LTD").split(" "),
);
/** Lower case inside a name, as in "Bank of America". */
const JOINING = new Set(["OF", "AND", "THE", "FOR", "IN", "ON", "AT", "TO", "BY", "DE", "LA", "EL", "DEL"]);
/** Longer initialisms with vowels, so nothing else tells them from a word. */
const INITIALISMS = new Set(["CBRE", "CACI", "IDEX", "IQVIA", "AECOM", "GATX", "HDFC", "HSBC", "IMAX", "KDDI", "MBIA", "MGIC", "TTEC", "REIT", "RELX", "PIMCO"]);

function caseWord(w: string, first: boolean): string {
  if (/[a-z]/.test(w)) return w; // a tail the filer already wrote
  const bare = w.replace(/\.$/, "");
  const dot = w.slice(bare.length);
  if (/\.[A-Z]/.test(bare)) return w; // U.S., W.W., E.ON
  if (/^\d+(ST|ND|RD|TH)$/.test(bare)) return bare.toLowerCase() + dot; // 1ST
  if (/[\d&]/.test(bare) || /^[IVX]+$/.test(bare)) return w; // 3M, AT&T, S&P, III
  if (!first && JOINING.has(bare)) return bare.toLowerCase() + dot;
  if (bare.length <= 3 && !SHORT_WORDS.has(bare)) return w;
  if (INITIALISMS.has(bare) || !/[AEIOUY]/.test(bare)) return w;
  if (/^MC[A-Z]{3,}$/.test(bare)) return `Mc${bare[2]}${bare.slice(3).toLowerCase()}${dot}`; // McKesson
  if (/^[OD]'[A-Z]/.test(bare)) return `${bare.slice(0, 3)}${bare.slice(3).toLowerCase()}${dot}`; // O'Neill
  return bare[0]! + bare.slice(1).toLowerCase() + dot;
}

function titleCase(s: string, startsName = true): string {
  let first = startsName;
  return s.replace(/[A-Za-z0-9][A-Za-z0-9'.&]*/g, (w) => {
    const out = caseWord(w, first);
    first = false;
    return out;
  });
}

export function companyName(raw: string): string {
  let s = raw.replace(/[’`]/g, "'").trim().replace(/^THE\s+/i, "");
  // tags and suffixes come off together: "NESTLE S A /ADR/", "Co., Ltd.", "HORTON D R INC /DE/"
  for (let i = 0; i < 4; i++) {
    const next = s.replace(TAG, "").replace(/\s*[/\\]\s*$/, "").replace(BARE_TAG, "$1").replace(SUFFIX, "").replace(/[,\s&]+$/, "");
    if (next === s || !next) break;
    s = next;
  }
  const key = s.toUpperCase();
  if (EXACT[key]) return EXACT[key]!;
  for (const [k, v] of Object.entries(BRANDS)) {
    if (key === k) return v;
    if (key.startsWith(k + " ")) return v + titleCase(s.slice(k.length), false);
  }
  if (/[a-z]/.test(s)) return s;
  // the SEC files people surname first: BERKLEY W R, HUNT J B TRANSPORT SERVICES
  const reversed = /^([A-Z]{2,}) ([A-Z]) ([A-Z])(?= |$)(.*)$/.exec(s);
  if (reversed) return `${reversed[2]}.${reversed[3]}. ${titleCase(reversed[1]! + reversed[4]!)}`;
  const initials = /^([A-Z]) ([A-Z]) (.+)$/.exec(s);
  if (initials) return `${initials[1]}.${initials[2]}. ${titleCase(initials[3]!)}`;
  // "C. H. ROBINSON": initials written with stops, closed up
  return titleCase(s.replace(/\b([A-Z])\. (?=[A-Z]\.)/g, "$1."));
}

/* Segment labels are derived from XBRL member names, and many filers run a joining word into the
 * word before it (jpm:AssetandWealthManagementSegmentMember). Stored labels carry that until
 * `facts-fetcher-segments --rebuild`, so the display repairs them. Port of facts-fetcher labels.py
 * `_unjoin`: change both. Real words ending in these letters stay whole. */
const NOT_JOINED = new Set([
  "brand", "broadband", "wideband", "narrowband", "baseband", "boadband", "cobrand", "band", "sand", "demand", "ondemand", "stand",
  "understand", "withstand", "command", "telecommand", "rand", "strand", "grand", "expand", "hand", "wand", "thousand", "ligand",
  "remand", "gland", "bland", "zealand", "thailand", "holland", "roof", "proof", "hoof", "aloof", "spoof", "thereof", "whereof",
  "hereof", "waterproof", "fireproof", "lathe", "bathe", "breathe", "clothe", "soothe", "therefor", "herewith", "therewith", "forthwith",
]);
const JOINED = /^([A-Za-z]+?)(and|of|the|for|with|excluding|including)$/;
const JOINED_EXACT: Record<string, string[]> = {
  businessto: ["Business", "to"], markto: ["Mark", "to"], subjectto: ["Subject", "to"],
  chargeon: ["Charge", "on"], chargeson: ["Charges", "on"], losson: ["Loss", "on"], incomeon: ["Income", "on"],
};
/** facts-fetcher data/member_labels.csv's curated labels, for labels stored before them. */
const SEGMENT_LABELS: Record<string, string> = {
  "Assetand Wealth Management": "Asset & Wealth Management",
  "Consumer Community Banking": "Consumer & Community Banking",
};

function unjoin(word: string): string[] {
  const low = word.toLowerCase();
  if (JOINED_EXACT[low]) return JOINED_EXACT[low]!;
  if (NOT_JOINED.has(low)) return [word];
  const m = JOINED.exec(word);
  if (!m) return [word];
  const head = m[1]!;
  const joiner = m[2]!;
  // "-land" is usually a place (Portland, Ireland): only a word ending al, il or ll runs into "and"
  if (joiner === "and" && /l$/i.test(head) && !/(al|il|ll)$/i.test(head)) return [word];
  return [...unjoin(head), joiner];
}

export function segmentLabel(raw: string): string {
  if (SEGMENT_LABELS[raw]) return SEGMENT_LABELS[raw]!;
  const words = raw.split(" ");
  return words.flatMap((w, i) => (i < words.length - 1 && /^[A-Za-z]?[a-z]+$/.test(w) ? unjoin(w) : [w])).join(" ");
}

/** Deep-copies API JSON with display names on every object that has a company_id, and repaired
 * labels on segment rows. */
export function displayNames<T>(data: T): T {
  if (Array.isArray(data)) return data.map(displayNames) as T;
  if (data && typeof data === "object") {
    const o = Object.fromEntries(Object.entries(data).map(([k, v]) => [k, displayNames(v)])) as Record<string, unknown>;
    if (typeof o.company_id === "string" && typeof o.name === "string") o.name = companyName(o.name);
    if (typeof o.member_label === "string") o.member_label = segmentLabel(o.member_label);
    return o as T;
  }
  return data;
}
