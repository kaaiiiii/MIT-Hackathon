export type CallStatus =
  | "created"
  | "connecting"
  | "disclosure"
  | "job_presentation"
  | "quote_collection"
  | "quote_clarification"
  | "summary_confirmation"
  | "complete"
  | "callback_required"
  | "declined"
  | "incomplete"
  | "failed";

export type OutcomeType =
  | "complete_quote"
  | "callback_required"
  | "declined"
  | "incomplete_quote";

export interface CreateCallInput {
  job_spec_version_id: string;
  vendor_id: string;
  call_type?: string;
}

export interface CallRecord {
  call_id: string;
  vendor_id: string;
  job_spec_version_id: string;
  spec_sha256: string;
  call_type: string;
  status: CallStatus;
  transcript_id: string;
  recording_id: string | null;
  final_outcome: OutcomeType | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

export interface TranscriptEvent {
  event_id: string;
  call_id: string;
  speaker: "agent" | "vendor" | "system";
  text: string;
  timestamp_seconds: number;
  sequence: number;
  created_at: string;
}

export interface StructuredCallOutcome {
  outcome_id: string;
  call_id: string;
  outcome_type: OutcomeType;
  quote_version_id: string | null;
  reason: string | null;
  validation_warnings: string[];
  created_at: string;
}

export interface CallView {
  call: CallRecord;
  transcript: TranscriptEvent[];
  original_quote: {
    quote_version_id: string;
    call_id: string;
    version: number;
    status: "draft" | "final";
    line_items: unknown[];
    terms: unknown[];
  };
  outcome: StructuredCallOutcome | null;
  recording_reference: string | null;
}

