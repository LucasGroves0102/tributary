import { describe, expect, it } from "vitest";

import { companyName, displayNames, segmentLabel } from "./displayNames";

// the SEC's conformed names, as silver.companies stores them
describe("companyName", () => {
  it.each([
    // what the audit found
    ["MCKESSON CORP", "McKesson"],
    ["AT&T INC.", "AT&T"],
    ["FEDEX CORP", "FedEx"],
    ["SCHWAB CHARLES CORP", "Charles Schwab"],
    ["HNI CORP", "HNI"],
    ["TOYOTA MOTOR CORP/", "Toyota Motor"],
    ["WELLS FARGO & COMPANY/MN", "Wells Fargo"],
    ["US BANCORP DE", "U.S. Bancorp"],
    ["KEYCORP /NEW/", "KeyCorp"],
    ["V F CORP", "VF"],
    ["AAR CORP", "AAR"],
    // state and listing tags
    ["COSTCO WHOLESALE CORP /NEW", "Costco Wholesale"],
    ["BANK OF AMERICA CORP /DE/", "Bank of America"],
    ["PROGRESSIVE CORP/OH/", "Progressive"],
    ["VALERO ENERGY CORP/TX", "Valero Energy"],
    ["VERTEX PHARMACEUTICALS INC / MA", "Vertex Pharmaceuticals"],
    ["Jefferson Capital, Inc. / DE", "Jefferson Capital"],
    ["LIFECORE BIOMEDICAL, INC. DE", "Lifecore Biomedical"],
    ["BROADWAY FINANCIAL CORP DE", "Broadway Financial"],
    ["SOUTHERN COPPER CORP/", "Southern Copper"],
    ["Schaeffler AG/ADR", "Schaeffler"],
    ["SOLVAY S A /ADR/", "Solvay"],
    ["Anheuser-Busch InBev SA/NV", "Anheuser-Busch InBev"],
    ["SLB LIMITED/NV", "SLB"],
    ["Blue Acquisition Corp/Cayman", "Blue Acquisition"],
    ["ACUITY INC. (DE)", "Acuity"],
    // a slash inside the name stays
    ["M/I HOMES, INC.", "M/I Homes"],
    ["RiverNorth/DoubleLine Strategic Opportunity Fund, Inc.", "RiverNorth/DoubleLine Strategic Opportunity Fund"],
    // legal suffixes
    ["JPMORGAN CHASE & CO", "JPMorgan Chase"],
    ["ELI LILLY & Co", "Eli Lilly"],
    ["Merck & Co., Inc.", "Merck"],
    ["CRH PUBLIC LTD CO", "CRH"],
    ["Osotspa Public Co Limited/ADR", "Osotspa"],
    ["Toho Co., Ltd./ADR", "Toho"],
    ["ENTERPRISE PRODUCTS PARTNERS L.P.", "Enterprise Products Partners"],
    ["Cadeler A/S", "Cadeler"],
    ["TELEFONICA S A", "Telefonica"],
    ["NATUZZI S P A", "Natuzzi"],
    ["ING GROEP NV", "ING Groep"],
    ["E.ON SE", "E.ON"],
    // surname first, and initials
    ["BERKLEY W R CORP", "W.R. Berkley"],
    ["HORTON D R INC /DE/", "D.R. Horton"],
    ["HUNT J B TRANSPORT SERVICES INC", "J.B. Hunt Transport Services"],
    ["SMITH A O CORP", "A.O. Smith"],
    ["FULLER H B CO", "H.B. Fuller"],
    ["PRICE T ROWE GROUP INC", "T. Rowe Price Group"],
    ["J M SMUCKER Co", "J.M. Smucker"],
    ["U S PHYSICAL THERAPY INC /NV", "U.S. Physical Therapy"],
    ["C. H. ROBINSON WORLDWIDE, INC.", "C.H. Robinson Worldwide"],
    ["W.W. GRAINGER, INC.", "W.W. Grainger"],
    ["U.S. GOLD CORP.", "U.S. Gold"],
    ["AIR T INC", "Air T"],
    ["FIDELITY D & D BANCORP INC", "Fidelity D & D Bancorp"],
    // initialisms stay capitals; short words don't
    ["CBRE GROUP, INC.", "CBRE Group"],
    ["KLA CORP", "KLA"],
    ["EOG RESOURCES INC", "EOG Resources"],
    ["AES CORP", "AES"],
    ["HP INC", "HP"],
    ["NVR INC", "NVR"],
    ["3M CO", "3M"],
    ["PG&E Corp", "PG&E"],
    ["H&R BLOCK INC", "H&R Block"],
    ["TD SYNNEX CORP", "TD Synnex"],
    ["CVS HEALTH Corp", "CVS Health"],
    ["CACI INTERNATIONAL INC /DE/", "CACI International"],
    ["IQVIA HOLDINGS INC.", "IQVIA Holdings"],
    ["AECOM", "AECOM"],
    ["GROUP 1 AUTOMOTIVE INC", "Group 1 Automotive"],
    ["BEST BUY CO INC", "Best Buy"],
    ["GAP INC", "Gap"],
    ["DOW INC.", "Dow"],
    ["AIR PRODUCTS", "Air Products"],
    ["EXPEDITORS INTERNATIONAL OF WASHINGTON INC", "Expeditors International of Washington"],
    // brands and the SEC's spellings
    ["CONOCOPHILLIPS", "ConocoPhillips"],
    ["FREEPORT-MCMORAN INC", "Freeport-McMoRan"],
    ["MARSH & MCLENNAN COMPANIES, INC.", "Marsh & McLennan Companies"],
    ["L3HARRIS TECHNOLOGIES, INC. /DE/", "L3Harris Technologies"],
    ["EBAY INC", "eBay"],
    ["O REILLY AUTOMOTIVE INC", "O'Reilly Automotive"],
    ["LOWES COMPANIES INC", "Lowe's Companies"],
    ["KOHLS Corp", "Kohl's"],
    ["SHERWIN WILLIAMS CO", "Sherwin-Williams"],
    ["CAMPBELL'S Co", "Campbell's"],
    ["DICK'S SPORTING GOODS, INC.", "Dick's Sporting Goods"],
    ["NEWS CORP", "News Corp"],
    ["SOUTHERN CO", "Southern Company"],
    ["FEDERAL NATIONAL MORTGAGE ASSOCIATION FANNIE MAE", "Fannie Mae"],
    // a name the filer already wrote is left as written
    ["lululemon athletica inc.", "lululemon athletica"],
    ["Bank of New York Mellon Corp", "Bank of New York Mellon"],
    ["KKR & Co. Inc.", "KKR"],
    ["XPO, Inc.", "XPO"],
    ["FedEx Freight Holding Company, Inc.", "FedEx Freight Holding"],
  ])("%s → %s", (raw, want) => expect(companyName(raw)).toBe(want));

  it("never turns a name into nothing", () => {
    for (const raw of ["CO", "INC", "Co.", "/DE/", "A/S"]) expect(companyName(raw)).not.toBe("");
  });
});

// labels as gold.segments stores them today, from filers' XBRL member names
describe("segmentLabel", () => {
  it.each([
    ["Assetand Wealth Management", "Asset & Wealth Management"],
    ["Consumer Community Banking", "Consumer & Community Banking"],
    ["Commercial and Investment Bank", "Commercial and Investment Bank"],
    ["Advertisingand Marketing", "Advertising and Marketing"],
    ["Agriculturaland Metals Futuresand Options Contracts", "Agricultural and Metals Futures and Options Contracts"],
    ["Oiland Gas", "Oil and Gas"],
    ["Retailand Wholesale", "Retail and Wholesale"],
    ["Productsand Services", "Products and Services"],
    ["Researchand Development", "Research and Development"],
    ["Salesof Goods", "Sales of Goods"],
    ["Restofthe World", "Rest of the World"],
    ["Americasexcludingthe United States", "Americas excluding the United States"],
    ["Businessto Business", "Business to Business"],
    // real words, whatever they end in
    ["Brand Revenue", "Brand Revenue"],
    ["Broadband Services", "Broadband Services"],
    ["Land Sales", "Land Sales"],
    ["Ireland Operations", "Ireland Operations"],
    ["New Zealand Dairy", "New Zealand Dairy"],
    ["Thailand Plant", "Thailand Plant"],
    ["Portland General", "Portland General"],
    ["Sand Mining", "Sand Mining"],
    ["Demand Response", "Demand Response"],
    ["Roof Tiles", "Roof Tiles"],
    // the last word has nothing to join
    ["Mainland", "Mainland"],
    ["Other Brand", "Other Brand"],
  ])("%s → %s", (raw, want) => expect(segmentLabel(raw)).toBe(want));

  it("repairs segment rows as they arrive, and no other name", () => {
    expect(displayNames({ rows: [{ member: "jpm:X", member_label: "Assetand Wealth Management", label: "Assetand" }] })).toEqual({
      rows: [{ member: "jpm:X", member_label: "Asset & Wealth Management", label: "Assetand" }] });
  });
});
