-- Sectors (046): Trane Technologies, an HVAC maker, read as Technology (Riley, test 6, #43). Its SIC
-- is 3822 ("auto controls for regulating residential and commercial environments": thermostats and
-- HVAC controls), inside 046's 3820-3825 instruments range, not 3585 (air-conditioning and heating
-- equipment: Carrier, Lennox, Johnson Controls), which 046 already put in Industrials, Building &
-- office products. 3822 joins 3585's industry. Measured on hermes (2026-11-17, read-only): Trane is
-- the one registrant at 3822.
--
-- Thermo Fisher stays in Technology: its SIC is 3829 ("measuring and controlling devices, NEC"),
-- not 3826 (laboratory analytical instruments: Agilent, Waters, Illumina, Bruker), which 046 already
-- put in Health Care. 3829's eleven registrants are Thermo Fisher, Quantum-Si, Niki BioSolutions
-- and Owlet (health) beside Rockwell Automation, Trimble, Onto Innovation, Mirion, Geospace,
-- Autoscope and Birchtech (industrial and technology instruments): moving the code would move
-- those seven, and a SIC range can't hold one company apart (the owner's call).
--
-- Same VALUES 4-tuples as 046 (services/query-api/tests/test_sectors.py reads 046 and this file:
-- ranges never overlap, every assigned SIC code has one sector). The SIC code and its label stay
-- visible everywhere, as before.
--
-- Locks on hermes (lock_timeout 3 s): one row deleted and three inserted in silver.sic_sectors
-- (ROW EXCLUSIVE; the screener and peers read it). Each Query API process reads /v1/sectors once
-- (a restart, as the deploy does, shows the change). Undo: delete the three rows, insert
-- ('3820', '3825', 'Technology', 'Electronic instruments & controls').

SET LOCAL lock_timeout = '3s';

DELETE FROM silver.sic_sectors WHERE sic_from = '3820';

INSERT INTO silver.sic_sectors (sic_from, sic_to, sector, industry) VALUES
    ('3820', '3821', 'Technology', 'Electronic instruments & controls'),
    ('3822', '3822', 'Industrials', 'Building & office products'),
    ('3823', '3825', 'Technology', 'Electronic instruments & controls');
