import Link from "next/link";

export default function HomePage() {
  return (
    <main>
      <section className="pokedex-shell">
        <div className="pokedex-top">
          <div className="lights" aria-hidden>
            <span className="light blue" />
            <span className="light red" />
            <span className="light yellow" />
            <span className="light green" />
          </div>
          <span className="brand">KANTO DEX UNIT</span>
        </div>

        <div className="pokedex-body">
          <div className="screen">
            <div className="screen-inner screen-stack">
              <h1>POKE SCAN</h1>
              <p className="muted">Capture a Pokemon card and run OCR + local matching.</p>
              <div className="button-row">
                <Link className="btn" href="/scan">
                  OPEN SCANNER
                </Link>
              </div>
            </div>
          </div>

          <section className="section">
            <h2>MODE</h2>
            <p className="muted">Local-first demo. No pricing provider connected yet.</p>
          </section>
        </div>
        <div className="hinge" />
      </section>
    </main>
  );
}
