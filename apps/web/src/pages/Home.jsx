import { Link } from 'react-router-dom';
import EstimatorIntake from '../components/EstimatorIntake';
import './home.css';

const DOCKET = [
  ['09:12', 'Intake — you talk once.', 'One short call about your move. That is your only job.'],
  ['13:40', 'Calls to vendors placed — we work the phones.', 'Unnecessary fees are challenged on the record; real quotes get played against each other.'],
  ['18:20', 'Report filed — you pick.', 'One report: the best deal, the proof, and exactly what to do next.'],
];

export default function Home() {
  return (
    <main className="home">
      <h1 className="home__hero">
        We call the movers
        <br />
        <span className="home__hero-accent">so you don't have to.</span>
      </h1>
      <p className="home__sub">
        Tell our agent about your move once. We phone every nearby moving company,
        negotiate the price down on the record, and hand you one clear answer.
      </p>

      <EstimatorIntake />

      <section className="docket" aria-label="How it works">
        <h2 className="micro muted docket__title">How it works — the day's docket</h2>
        {DOCKET.map(([stamp, head, body]) => (
          <div className="docket__row" key={stamp}>
            <span className="docket__stamp">{stamp}</span>
            <p className="docket__body">
              <strong>{head}</strong> {body}
            </p>
          </div>
        ))}
      </section>

      <div className="home__bridge">
        <p className="home__bridge-copy">
          <strong>Already ran a negotiation?</strong>
          <span className="mono muted">
            {' '}
            A real sample run: 2-bedroom move, Rock Hill SC to Charlotte NC, six companies
            called, $605 negotiated off the winner.
          </span>
        </p>
        <Link to="/report" className="btn">
          See a finished report →
        </Link>
      </div>

      <footer className="home__trust micro muted">
        Discloses it's an AI · Every number cites a recording · Too-cheap quotes get
        flagged
      </footer>
    </main>
  );
}
