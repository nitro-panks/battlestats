import { shipClass, nationLabel } from '../shipIdentity';

describe('shipClass', () => {
    it('maps every backend ship_type string to its WoWs class tag', () => {
        expect(shipClass('Destroyer')).toEqual({ abbr: 'DD', label: 'Destroyer' });
        expect(shipClass('Cruiser')).toEqual({ abbr: 'CA', label: 'Cruiser' });
        expect(shipClass('Battleship')).toEqual({ abbr: 'BB', label: 'Battleship' });
        expect(shipClass('Aircraft Carrier')).toEqual({ abbr: 'CV', label: 'Aircraft Carrier' });
        expect(shipClass('Submarine')).toEqual({ abbr: 'SS', label: 'Submarine' });
    });

    it('normalizes the WG AirCarrier alias to a carrier', () => {
        expect(shipClass('AirCarrier')).toEqual({ abbr: 'CV', label: 'Aircraft Carrier' });
    });

    it('returns null for unknown or absent class so the caller omits the glyph', () => {
        expect(shipClass(null)).toBeNull();
        expect(shipClass(undefined)).toBeNull();
        expect(shipClass('')).toBeNull();
        expect(shipClass('Unknown')).toBeNull();
        expect(shipClass('Galleon')).toBeNull();
    });
});

describe('nationLabel', () => {
    it('maps known WG nation codes to readable labels', () => {
        expect(nationLabel('japan')).toBe('Japan');
        expect(nationLabel('usa')).toBe('USA');
        expect(nationLabel('ussr')).toBe('USSR');
        expect(nationLabel('uk')).toBe('U.K.');
        expect(nationLabel('pan_asia')).toBe('Pan-Asia');
    });

    it('is case-insensitive and trims', () => {
        expect(nationLabel('  Japan ')).toBe('Japan');
    });

    it('falls back to a capitalized code for an unknown nation (chip still shows)', () => {
        expect(nationLabel('atlantis')).toBe('Atlantis');
    });

    it('returns null only for absent/empty nation', () => {
        expect(nationLabel(null)).toBeNull();
        expect(nationLabel(undefined)).toBeNull();
        expect(nationLabel('   ')).toBeNull();
    });
});
