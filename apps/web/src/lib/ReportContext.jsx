// ReportContext — owns which report is showing (sample / upload / demo replay)
// and drives the demo: replays events.jsonl on a timer to animate the blotter.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { applyEvent, buildRanking, parseJsonl } from './report';
import { getReportContext, prepareReport } from './api';
import { backendContextToReport } from './backendReport';

const DEMO_TICK_MS = 2400; // one event landing every ~2.4s, deliberate

const ReportCtx = createContext(null);

export function useReport() {
  return useContext(ReportCtx);
}

export function ReportProvider({ children }) {
  const [sample, setSample] = useState(null);
  const [backend, setBackend] = useState(null); // { versionId, report }
  const [backendError, setBackendError] = useState(null);
  const [preparingReport, setPreparingReport] = useState(false);
  const [upload, setUpload] = useState(null); // { name, report }
  const [uploadError, setUploadError] = useState(null);

  const [demo, setDemo] = useState({ active: false, step: 0, total: 0 });
  const [demoReport, setDemoReport] = useState(null);
  const [lastEvent, setLastEvent] = useState(null); // drives the blotter animation
  const demoAssets = useRef(null); // { base, events }
  const timer = useRef(null);

  useEffect(() => {
    fetch('/quotes.json')
      .then((r) => r.json())
      .then(setSample)
      .catch(() => setSample(null));
    const params = new URLSearchParams(window.location.search);
    const versionId = params.get('spec') || localStorage.getItem('nego_job_spec_version_id');
    if (versionId) {
      getReportContext(versionId)
        .then((context) => setBackend({ versionId, report: backendContextToReport(context) }))
        .catch((err) => setBackendError(err.message));
    }
    return () => clearInterval(timer.current);
  }, []);

  const loadBackendReport = useCallback(async (versionId) => {
    setBackendError(null);
    const context = await getReportContext(versionId);
    const loaded = { versionId, report: backendContextToReport(context) };
    setBackend(loaded);
    localStorage.setItem('nego_job_spec_version_id', versionId);
    return loaded.report;
  }, []);

  const refreshBackendReport = useCallback(async () => {
    if (!backend?.versionId) return null;
    return loadBackendReport(backend.versionId);
  }, [backend?.versionId, loadBackendReport]);

  const prepareBackendReport = useCallback(async () => {
    if (!backend?.versionId || preparingReport) return null;
    setBackendError(null);
    setPreparingReport(true);
    try {
      const context = await prepareReport(backend.versionId);
      const loaded = { versionId: backend.versionId, report: backendContextToReport(context) };
      setBackend(loaded);
      return loaded.report;
    } catch (err) {
      setBackendError(err.message);
      return null;
    } finally {
      setPreparingReport(false);
    }
  }, [backend?.versionId, preparingReport]);

  const stopDemo = useCallback(() => {
    clearInterval(timer.current);
    timer.current = null;
    setDemo({ active: false, step: 0, total: 0 });
    setDemoReport(null);
    setLastEvent(null);
  }, []);

  const startDemo = useCallback(async () => {
    clearInterval(timer.current);
    if (!demoAssets.current) {
      const [base, eventsText] = await Promise.all([
        fetch('/demo-base.json').then((r) => r.json()),
        fetch('/events.jsonl').then((r) => r.text()),
      ]);
      demoAssets.current = { base, events: parseJsonl(eventsText) };
    }
    const { base, events } = demoAssets.current;
    setDemoReport(base);
    setLastEvent(null);
    setDemo({ active: true, step: 0, total: events.length });
    let i = 0;
    timer.current = setInterval(() => {
      if (i >= events.length) {
        clearInterval(timer.current);
        timer.current = null;
        return;
      }
      const event = events[i];
      i += 1;
      setDemoReport((prev) => applyEvent(prev, event));
      setLastEvent({ ...event, seq: i });
      setDemo({ active: true, step: i, total: events.length });
    }, DEMO_TICK_MS);
  }, []);

  const loadUpload = useCallback((file) => {
    setUploadError(null);
    file
      .text()
      .then((text) => {
        const parsed = JSON.parse(text);
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          throw new Error('expected a JSON object');
        }
        setUpload({ name: file.name, report: parsed });
      })
      .catch((err) => setUploadError(`Could not parse JSON: ${err.message}`));
  }, []);

  const clearUpload = useCallback(() => {
    setUpload(null);
    setUploadError(null);
  }, []);

  const report = demo.active ? demoReport : upload ? upload.report : backend ? backend.report : sample;
  const source = demo.active ? 'demo' : upload ? 'upload' : backend ? 'backend' : 'sample';
  const ranking = useMemo(() => (report ? buildRanking(report) : []), [report]);

  const download = useCallback(() => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${report.report_id ?? 'negotiator_report'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [report]);

  const value = {
    report,
    ranking,
    source,
    uploadName: upload?.name ?? null,
    uploadError,
    backendError,
    backendVersionId: backend?.versionId ?? null,
    loadBackendReport,
    refreshBackendReport,
    prepareBackendReport,
    preparingReport,
    loadUpload,
    clearUpload,
    download,
    demo,
    lastEvent,
    startDemo,
    stopDemo,
  };

  return <ReportCtx.Provider value={value}>{children}</ReportCtx.Provider>;
}
