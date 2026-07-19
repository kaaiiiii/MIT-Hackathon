function evidencedValues(spec) {
  const fields = spec?.facts?.fields ?? {};
  return Object.fromEntries(
    Object.entries(fields).map(([name, field]) => [
      name,
      field && typeof field === 'object' && 'value' in field ? field.value : field,
    ]),
  );
}

function latestTerm(terms, category) {
  return [...(terms ?? [])].reverse().find((term) => term.category === category)?.value ?? null;
}

function numericTerms(terms, category) {
  return (terms ?? [])
    .filter((term) => term.category === category && typeof term.value === 'number')
    .map((term) => term.value);
}

function callForFrontend(call) {
  const totals = numericTerms(call.terms, 'estimated_total');
  const redFlags = (call.terms ?? [])
    .filter((term) => term.category === 'red_flag')
    .map((term) => ({
      key: term.key,
      detail: String(term.value),
      evidence_event_id: term.transcript_event_id,
    }));
  return {
    call_id: call.call_id,
    vendor: { vendor_id: call.vendor_id, name: call.vendor_id },
    status: call.status,
    recording_url: call.recording_reference,
    transcript: call.transcript ?? [],
    outcome: {
      outcome_type: call.outcome_type ?? 'incomplete_quote',
      reason: call.outcome_reason,
      validation_warnings: call.validation_warnings ?? [],
    },
    quote: {
      initial_total: totals[0] ?? null,
      estimated_total: totals.at(-1) ?? null,
      binding_status: latestTerm(call.terms, 'binding_status'),
      availability: latestTerm(call.terms, 'availability'),
      itemization_status: latestTerm(call.terms, 'itemization_status'),
      line_items: (call.line_items ?? []).map((item) => ({
        ...item,
        evidence_event_id: item.transcript_event_id,
      })),
      red_flags: redFlags,
      killed_fees: [],
      negotiation_moves: [],
    },
  };
}
export function backendContextToReport(context) {
  const values = evidencedValues(context.confirmed_job_spec);
  const research = context.final_research ?? context.estimator_research;
  return {
    report_id: `report_${context.job_spec_version_id}`,
    generated_at: research?.created_at ?? new Date().toISOString(),
    job_spec: {
      version_id: context.job_spec_version_id,
      spec_sha256: context.confirmed_job_spec?.canonical_hash ?? null,
      facts: {
        ...values,
        service: values.service,
        origin: values['origin.location'],
        destination: values['destination.location'],
        requested_date: values.requested_date,
      },
      evidence: context.confirmed_job_spec?.facts?.fields ?? {},
    },
    benchmarks: {},
    calls: (context.completed_calls ?? []).map(callForFrontend),
    recommendation: null,
    next_steps: (context.completed_calls ?? [])
      .filter((call) => call.outcome_type === 'callback_required')
      .map((call) => ({
        action: `Follow up with ${call.vendor_id}`,
        due: latestTerm(call.terms, 'callback') ?? 'Not provided',
        status: 'pending',
        call_id: call.call_id,
      })),
    honesty: {
      asked_if_ai: [],
      bids_cited_as_leverage: [],
      constraint_note:
        'Research is advisory context. Every quote value shown here comes from stored call evidence.',
    },
    research_context: {
      estimator: context.estimator_research,
      final: context.final_research,
    },
  };
}
