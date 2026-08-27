/**
 * Style de fond de carte, assemblé côté serveur.
 *
 * La clé MapTiler n'est pas une variable `NEXT_PUBLIC_*` : elle n'est donc pas figée à la
 * construction de l'image et se remplace sans rebuild. Elle reste visible dans les requêtes de
 * tuiles émises par le navigateur — c'est inhérent au rendu vectoriel côté client ; la protection
 * est la restriction par domaine, à configurer dans MapTiler.
 */

export const dynamic = "force-dynamic";

const STYLE_ID = /^[A-Za-z0-9._-]{1,64}$/;
const API_KEY = /^[A-Za-z0-9._-]{8,128}$/;

export function GET(): Response {
  const key = process.env.EZER_MAPTILER_KEY?.trim();
  const style = process.env.EZER_MAPTILER_STYLE?.trim() || "streets-v2-dark";
  const usable = key !== undefined && API_KEY.test(key) && STYLE_ID.test(style);
  return Response.json(
    {
      styleUrl: usable
        ? `https://api.maptiler.com/maps/${style}/style.json?key=${encodeURIComponent(key)}`
        : null,
    },
    { headers: { "Cache-Control": "no-store, max-age=0" } },
  );
}
