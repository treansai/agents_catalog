import { haversineKm, nominatimPlace, simplify, straightLine } from "./places";

describe("Lieux et itinéraires", () => {
  it("lit une réponse Nominatim et borne les coordonnées", () => {
    const place = nominatimPlace(
      [{ lat: "48.8582599", lon: "2.2945006", display_name: "Tour Eiffel, Paris" }],
      "Tour Eiffel"
    );
    expect(place).toEqual({ name: "Tour Eiffel, Paris", lon: 2.294_501, lat: 48.858_26 });
  });

  it("refuse une réponse vide ou hors du globe plutôt que d'inventer un point", () => {
    expect(nominatimPlace([], "nulle part")).toBeUndefined();
    expect(nominatimPlace([{ lat: "999", lon: "0" }], "nulle part")).toBeUndefined();
    expect(nominatimPlace({ error: "unauthorized" }, "nulle part")).toBeUndefined();
  });

  it("garde le nom demandé quand le fournisseur n'en renvoie pas", () => {
    expect(nominatimPlace([{ lat: "48.85", lon: "2.35" }], "Le Rival")?.name).toBe("Le Rival");
  });

  it("ramène une longue polyligne sous la borne du schéma, extrémités comprises", () => {
    const points: [number, number][] = Array.from({ length: 2_500 }, (_, index) => [
      2 + index / 10_000,
      48 + index / 10_000
    ]);
    const reduced = simplify(points);
    expect(reduced.length).toBeLessThanOrEqual(500);
    expect(reduced[0]).toEqual(points[0]);
    expect(reduced.at(-1)).toEqual(points.at(-1));
  });

  it("estime une distance et une durée cohérentes sans service de routage", () => {
    const gareDeLyon = { name: "Gare de Lyon", lon: 2.373_6, lat: 48.844_3 };
    const rival = { name: "Le Rival", lon: 2.353_2, lat: 48.860_6 };
    expect(haversineKm(gareDeLyon, rival)).toBeCloseTo(2.35, 1);

    const walk = straightLine(gareDeLyon, rival, "walking");
    const drive = straightLine(gareDeLyon, rival, "driving");
    expect(walk.durationMin).toBeGreaterThan(drive.durationMin);
    expect(walk.coordinates).toHaveLength(2);
  });
});
