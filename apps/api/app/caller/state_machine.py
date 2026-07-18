from __future__ import annotations

from .errors import InvalidTransitionError
from .schemas import CallStatus


TERMINAL_STATES = {
    CallStatus.COMPLETE,
    CallStatus.CALLBACK_REQUIRED,
    CallStatus.DECLINED,
    CallStatus.INCOMPLETE,
    CallStatus.FAILED,
}

ALLOWED_TRANSITIONS: dict[CallStatus, set[CallStatus]] = {
    CallStatus.CREATED: {CallStatus.CONNECTING},
    CallStatus.CONNECTING: {CallStatus.DISCLOSURE, CallStatus.FAILED},
    CallStatus.DISCLOSURE: {
        CallStatus.JOB_PRESENTATION,
        CallStatus.DECLINED,
        CallStatus.CALLBACK_REQUIRED,
        CallStatus.INCOMPLETE,
        CallStatus.FAILED,
    },
    CallStatus.JOB_PRESENTATION: {
        CallStatus.QUOTE_COLLECTION,
        CallStatus.DECLINED,
        CallStatus.CALLBACK_REQUIRED,
        CallStatus.INCOMPLETE,
        CallStatus.FAILED,
    },
    CallStatus.QUOTE_COLLECTION: {
        CallStatus.QUOTE_CLARIFICATION,
        CallStatus.DECLINED,
        CallStatus.CALLBACK_REQUIRED,
        CallStatus.INCOMPLETE,
        CallStatus.FAILED,
    },
    CallStatus.QUOTE_CLARIFICATION: {
        CallStatus.SUMMARY_CONFIRMATION,
        CallStatus.DECLINED,
        CallStatus.CALLBACK_REQUIRED,
        CallStatus.INCOMPLETE,
        CallStatus.FAILED,
    },
    CallStatus.SUMMARY_CONFIRMATION: {
        CallStatus.COMPLETE,
        CallStatus.CALLBACK_REQUIRED,
        CallStatus.DECLINED,
        CallStatus.INCOMPLETE,
        CallStatus.FAILED,
    },
}


class CallStateMachine:
    def assert_transition(self, current: CallStatus, target: CallStatus) -> None:
        if current in TERMINAL_STATES:
            raise InvalidTransitionError(f"Call is already terminal: {current.value}")
        if target not in ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError(
                f"Invalid call transition: {current.value} -> {target.value}"
            )

    def next_phase(self, current: CallStatus) -> CallStatus:
        ordered = {
            CallStatus.DISCLOSURE: CallStatus.JOB_PRESENTATION,
            CallStatus.JOB_PRESENTATION: CallStatus.QUOTE_COLLECTION,
            CallStatus.QUOTE_COLLECTION: CallStatus.QUOTE_CLARIFICATION,
            CallStatus.QUOTE_CLARIFICATION: CallStatus.SUMMARY_CONFIRMATION,
        }
        try:
            return ordered[current]
        except KeyError as exc:
            raise InvalidTransitionError(
                f"No conversational phase follows {current.value}"
            ) from exc
