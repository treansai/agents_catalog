export default function Loading() {
  return (
    <div className="loading-screen" role="status" aria-label="Chargement du tableau Ezer">
      <header className="topbar">
        <span className="brand">
          <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
          <span className="brand-name">ezer</span>
        </span>
      </header>
      <div className="loading-body">
        <div className="loading-line loading-line--short" />
        <div className="loading-line loading-line--title" />
        <div className="loading-line loading-line--title loading-line--offset" />
        <div className="loading-metrics">
          <span /><span /><span /><span />
        </div>
        <span className="sr-only">Ezer prépare les analyses récentes…</span>
      </div>
    </div>
  );
}
