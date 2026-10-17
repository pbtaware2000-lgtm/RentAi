"""Persistent floating RentAI assistant shared by all authenticated pages."""
import logging

import streamlit as st

from frontend.application_api import ApplicationAPIError, request

logger = logging.getLogger(__name__)
SUGGESTIONS = {
    "renter": ["How do I book a property?", "How can I compare properties?", "Where can I pay rent?"],
    "owner": ["How do I add a property?", "How can I accept a booking?", "Where can I review enquiries?"],
    "broker": ["How do I search properties?", "How do I compare properties?"],
    "admin": ["How do I use Admin & Reports?", "Where do I manage accounts?"],
}
PAGE_SUGGESTIONS = {
    "Search Properties": ["How do I filter listings?", "How can I compare properties?", "How do I save a property?"],
    "AI Rent Prediction": ["What details do I enter?", "How should I understand the estimate?"],
    "Compare Properties": ["How do I add properties to compare?", "Which details can I compare?"],
    "My Bookings": ["What does my booking status mean?", "When can I pay rent?"],
    "Pay Rent": ["How do I check my payment status?", "Where can I find booking status?"],
    "Add New Property": ["How do I publish a listing?", "How do I upload property photos?"],
    "My Properties": ["How do I edit a listing?", "How do I remove a listing?"],
    "Applications & Enquiries": ["Where do I review renter enquiries?", "How do I respond to an enquiry?"],
    "Booking Requests": ["How can I accept a booking?", "How can I reject a booking?"],
    "Revenue & Rent Collection": ["Where can I check payment statuses?", "What does a pending payment mean?"],
    "Maintenance Requests": ["Where do I review maintenance requests?", "How do I update a request status?"],
}


def _start_user_session(uid):
    if st.session_state.get("rentai_chat_uid") == uid:
        return
    for key in list(st.session_state.keys()):
        if key.startswith("rentai_chat_history_"):
            st.session_state.pop(key, None)
    for key in (
        "rentai_chat_pending", "rentai_chat_sending", "rentai_chat_draft",
        "rentai_chat_open", "rentai_chat_loaded_uid", "rentai_chat_error",
        "rentai_chat_clear_draft", "rentai_chat_uid",
    ):
        st.session_state.pop(key, None)
    st.session_state.rentai_chat_uid = uid


def _history(uid):
    history_key = f"rentai_chat_history_{uid}"
    if st.session_state.get("rentai_chat_loaded_uid") != uid:
        try:
            result = request("GET", "/chatbot/messages")
            if not isinstance(result, dict) or not isinstance(result.get("messages"), list):
                raise ApplicationAPIError("RentAI returned an invalid chat history response.")
            st.session_state[history_key] = result["messages"][-20:]
            st.session_state.rentai_chat_error = ""
        except ApplicationAPIError as exc:
            st.session_state[history_key] = []
            st.session_state.rentai_chat_error = str(exc)
        except Exception as exc:
            logger.exception("Unexpected error loading the chatbot history")
            st.session_state[history_key] = []
            st.session_state.rentai_chat_error = f"Could not load chat history ({type(exc).__name__})."
        st.session_state.rentai_chat_loaded_uid = uid
    return st.session_state.setdefault(history_key, [])


def _queue_draft():
    if st.session_state.get("rentai_chat_sending"):
        return
    draft = str(st.session_state.get("rentai_chat_draft", "")).strip()
    if draft:
        st.session_state.rentai_chat_pending = draft
        st.session_state.rentai_chat_error = ""
    else:
        st.session_state.rentai_chat_error = "Enter a message before sending."


def _send_pending(uid, page):
    question = st.session_state.get("rentai_chat_pending", "")
    if not question or st.session_state.get("rentai_chat_sending"):
        return
    st.session_state.rentai_chat_pending = ""
    st.session_state.rentai_chat_sending = True
    messages = st.session_state.setdefault(f"rentai_chat_history_{uid}", [])
    messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    try:
        result = request("POST", "/chatbot/messages", payload={"message": question, "page": page})
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("answer"), str)
            or not result["answer"].strip()
            or result.get("mode") != "RentAI Guide"
        ):
            raise ApplicationAPIError("RentAI returned an invalid chatbot response.")
        mode = result["mode"]
        messages[:] = [
            message for message in messages
            if not (
                message.get("role") == "assistant"
                and str(message.get("content", "")).startswith("I could not get a response:")
            )
        ]
        messages.append({"role": "assistant", "content": result["answer"], "mode": mode})
        st.session_state.rentai_chat_error = ""
        st.session_state.rentai_chat_clear_draft = True
        with st.chat_message("assistant"):
            st.caption(mode)
            st.markdown(result["answer"])
    except ApplicationAPIError as exc:
        st.session_state.rentai_chat_error = str(exc)
        messages.append({"role": "assistant", "content": f"I could not get a response: {exc}"})
        with st.chat_message("assistant"):
            st.error(f"I could not get a response: {exc}")
    except Exception as exc:
        logger.exception("Unexpected chatbot message submission error")
        message = "An unexpected error interrupted the request. Please try again."
        st.session_state.rentai_chat_error = message
        messages.append({"role": "assistant", "content": message})
        with st.chat_message("assistant"):
            st.error(message)
    finally:
        st.session_state.rentai_chat_sending = False


def render(role, uid, page=""):
    if role not in SUGGESTIONS:
        return
    _start_user_session(uid)
    if st.session_state.pop("rentai_chat_clear_draft", False):
        st.session_state.pop("rentai_chat_draft", None)

    st.markdown("""
    <style>
      .st-key-rentai_chat_launcher { position: fixed; right: 22px; bottom: 22px; z-index: 9998; width: auto; }
      .st-key-rentai_chat_launcher button { width: 54px; height: 54px; padding: 0; border: 0; border-radius: 50%; background: #ff6b1a; color: #fff; box-shadow: 0 6px 22px #07101f40; font-size: 23px; transition: transform .15s ease, box-shadow .15s ease; }
      .st-key-rentai_chat_launcher button:hover { transform: translateY(-2px); box-shadow: 0 9px 28px #07101f50; background: #f45e0d; color: #fff; }
      .st-key-rentai_chat_panel { position: fixed; right: 22px; bottom: 88px; z-index: 9999; width: min(380px, calc(100vw - 28px)); height: auto; max-height: min(520px, calc(100vh - 108px)); background: #fff; border: 1px solid #e2e8f0; border-radius: 16px; box-shadow: 0 18px 55px #07101f38; padding: 12px; overflow-y: auto; }
      .rentai-chat-header { background: #07101f; color: #fff; padding: 12px 14px; border-radius: 11px; font-weight: 700; }
      .rentai-chat-subtitle { color: #64748b; font-size: 12px; margin: 4px 2px 8px; }
      @media (max-width: 600px) { .st-key-rentai_chat_launcher { right: 14px; bottom: 14px; } .st-key-rentai_chat_panel { right: 8px; bottom: 78px; width: calc(100vw - 16px); height: auto; max-height: min(70vh, 520px); padding: 9px; } }
    </style>
    """, unsafe_allow_html=True)

    if not st.session_state.get("rentai_chat_open", False):
        with st.container(key="rentai_chat_launcher"):
            if st.button("💬", key="rentai_chat_open_button", help="Chat with RentAI Assistant"):
                st.session_state.rentai_chat_open = True
                st.rerun()
        return

    with st.container(key="rentai_chat_panel"):
        header_col, close_col = st.columns([5, 1])
        with header_col:
            st.markdown('<div class="rentai-chat-header">RentAI Assistant</div>', unsafe_allow_html=True)
        with close_col:
            if st.button("×", key="rentai_chat_close", help="Close chat"):
                st.session_state.rentai_chat_open = False
                st.rerun()
        st.markdown('<div class="rentai-chat-subtitle">RentAI help for your account</div>', unsafe_allow_html=True)

        messages = _history(uid)
        if st.session_state.get("rentai_chat_error") and not messages:
            st.error(st.session_state.rentai_chat_error)
        if not messages:
            with st.chat_message("assistant"):
                st.markdown("Welcome! I can guide you through RentAI features.")
            st.caption("Suggested questions")
            for index, suggestion in enumerate(PAGE_SUGGESTIONS.get(page, SUGGESTIONS[role])):
                if st.button(suggestion, key=f"rentai_chat_suggestion_{index}", use_container_width=True):
                    st.session_state.rentai_chat_pending = suggestion
                    break

        with st.container(border=False):
            for message in messages[-20:]:
                with st.chat_message("user" if message.get("role") == "user" else "assistant"):
                    if message.get("mode"):
                        st.caption(message["mode"])
                    st.markdown(message.get("content", ""))

        if messages:
            st.caption("Ask another question")
            for index, suggestion in enumerate(PAGE_SUGGESTIONS.get(page, SUGGESTIONS[role])):
                if st.button(
                    suggestion,
                    key=f"rentai_chat_followup_{index}",
                    use_container_width=True,
                    disabled=st.session_state.get("rentai_chat_sending", False),
                ):
                    st.session_state.rentai_chat_pending = suggestion
                    break

        if st.session_state.get("rentai_chat_sending"):
            st.info("Preparing RentAI guidance…")
        input_col, send_col = st.columns([4, 1])
        with input_col:
            st.text_input(
                "Message", placeholder="Ask about RentAI…", label_visibility="collapsed",
                max_chars=2000, key="rentai_chat_draft", on_change=_queue_draft,
                disabled=st.session_state.get("rentai_chat_sending", False),
            )
        with send_col:
            st.button(
                "Send", key="rentai_chat_send", type="primary", use_container_width=True,
                on_click=_queue_draft, disabled=st.session_state.get("rentai_chat_sending", False),
            )

        if st.session_state.get("rentai_chat_error") and messages:
            st.error(st.session_state.rentai_chat_error)
        if st.session_state.get("rentai_chat_pending"):
            with st.spinner("Preparing RentAI guidance…"):
                _send_pending(uid, page)
            st.rerun()

        if st.button("Clear Chat", key="rentai_chat_clear"):
            try:
                result = request("DELETE", "/chatbot/messages")
                if not isinstance(result, dict) or result.get("cleared") is not True:
                    raise ApplicationAPIError("RentAI did not confirm clearing chat history.")
                st.session_state[f"rentai_chat_history_{uid}"] = []
                st.session_state.rentai_chat_error = ""
                st.rerun()
            except ApplicationAPIError as exc:
                st.error(str(exc))
