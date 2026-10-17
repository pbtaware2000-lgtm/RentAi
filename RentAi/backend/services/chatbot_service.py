"""Private, deterministic RentAI help based on the signed-in role and page."""
import re

from backend.repositories.sqlite_repository import SQLiteRepository

MODE = "RentAI Guide"
REFUSAL = (
    "I can help with RentAI features such as finding properties, rent estimates, bookings, "
    "payments, enquiries, and owner listings. What would you like to do in RentAI?"
)
OUT_OF_SCOPE_PATTERNS = (
    r"\bjava\b", r"\bnewton(?:'s)? first law\b", r"\bsolve (?:this )?(?:equation|integral|derivative)\b",
    r"\bcapital of\b", r"\bwrite (?:me )?(?:a )?(?:poem|story|essay|code)\b",
)

PAGE_GUIDES = {
    "renter": {
        "Dashboard": "Your Renter Dashboard is the starting point for property search, rent estimates, saved homes, enquiries, bookings, payments, and profile tools. Choose a section from the Renter navigation to continue.",
        "Search Properties": "Use Search Properties to browse listings and narrow them with the available filters. Open a listing to review rent, location, amenities, photos, and availability. Save or compare suitable options; only eligible published vacant listings can receive booking requests.",
        "AI Rent Prediction": "Enter the property details requested on AI Rent Prediction and submit them to view an estimated rent. It is an estimate, not a listing price or guarantee.",
        "Compare Properties": "Select two or more properties from Search Properties or Saved Properties, then open Compare Properties to review them side by side. Compare location, monthly rent, amenities, and the other fields shown.",
        "Saved Properties": "Open Saved Properties to revisit listings you saved while browsing. Open a listing for its details or select properties to compare them.",
        "My Enquiries": "Open My Enquiries to review messages or enquiry status for properties you contacted. To ask about another listing, open it in Search Properties and use its enquiry option.",
        "My Bookings": "Open My Bookings to see booking requests and their current status. If an owner accepts, follow the payment instructions shown with that booking. Submitting a request does not confirm it.",
        "Pay Rent": "Use Pay Rent for a booking eligible for payment. Follow the existing Razorpay checkout and check the resulting payment status in My Bookings. A pending status does not mean payment completed.",
        "My Profile": "Open My Profile to review and update the profile fields available to your account, then save changes using the form controls on the page.",
    },
    "owner": {
        "Dashboard": "Your Owner Dashboard summarizes your portfolio and links to listings, enquiries, booking requests, rent collection, maintenance, analytics, and profile tools.",
        "My Properties": "Open My Properties to review your listings. Use the available edit or remove controls on a listing to manage it; use Add New Property to create another listing.",
        "Add New Property": "Complete the fields in Add New Property, upload photos through the property form if desired, and submit it. Review the listing status in My Properties; publishing depends on the options and validation shown by the form.",
        "Applications & Enquiries": "Open Applications & Enquiries to review renter questions and application details for your listings. Select an item to view its details and use the available response controls.",
        "Renter List": "Open Renter List to review renter records associated with your properties. Use the filters and detail views available on that page.",
        "Property Analytics": "Open Property Analytics to review the portfolio and performance charts available for your properties. Use the displayed filters to narrow the view.",
        "Revenue & Rent Collection": "Open Revenue & Rent Collection to review recorded rental payments and statuses. A pending status is not proof of a completed payment.",
        "Maintenance Requests": "Open Maintenance Requests to review renter-submitted items, inspect their details, and use the status controls shown on the page.",
        "My Profile": "Open My Profile to review and update the profile fields available to your account, then save changes using the form controls.",
        "Booking Requests": "Open Booking Requests to review incoming renter requests. Select a pending request, review its details, then choose Accept request or Reject request. The renter can see the updated status in My Bookings.",
        "Settings": "Open Settings to review account options currently available to you and use the controls shown there.",
    },
    "broker": {
        "Broker Dashboard": "Use the Broker Dashboard to open the property search, comparison, rent prediction, and classification tools available to your role. Actions are limited to the controls shown on those pages.",
    },
    "admin": {
        "Admin Dashboard": "Use Admin Dashboard and Admin & Reports to review the administrative summaries and reports available to your account. Account actions are limited to the controls shown there.",
        "Admin & Reports": "Open Admin & Reports to review application reports and management views. Use the filters and actions shown for your administrator account.",
    },
}


def is_out_of_scope(question: str) -> bool:
    return any(re.search(pattern, question, flags=re.IGNORECASE) for pattern in OUT_OF_SCOPE_PATTERNS)


def static_answer(role: str, page: str, question: str) -> str:
    """Return local RentAI guidance. This function never calls an AI provider."""
    if role not in PAGE_GUIDES:
        raise ValueError("Unsupported chatbot role")
    if is_out_of_scope(question):
        return REFUSAL

    text = question.casefold()
    if role == "owner" and re.search(r"\baccept\w*\b.*\bbook\w*\b|\bbook\w*\b.*\baccept\w*\b", text):
        return PAGE_GUIDES[role]["Booking Requests"]
    if role == "owner" and re.search(r"\breject\w*\b.*\bbook\w*\b|\bbook\w*\b.*\breject\w*\b", text):
        return "Open Booking Requests, select the pending request, review its details, then choose Reject request if that is your decision."
    if role == "renter" and re.search(r"\b(book|booking|booking request)\b", text):
        return "Open Search Properties, choose an eligible published listing marked vacant, and submit a booking request. The owner reviews it. Check My Bookings for the status; if accepted, follow the payment steps shown there."
    if role == "renter" and re.search(r"\b(recommend|recommendation|best|choose|which)\b", text) and re.search(r"\b(property|properties|rental|rentals|home|homes|apartment)\b", text):
        return "I can’t see your live listing results from this help chat, so I can’t name a specific best property. Open Search Properties and filter by location and monthly rent. Review amenities and nearby facilities, then use Compare Properties to compare your shortlisted options."
    if role == "owner" and re.search(r"\b(add|create|list|publish|upload|photo|image)\w*\b.*\b(property|listing|photo|image|home)\b", text):
        return PAGE_GUIDES[role]["Add New Property"]
    if role == "renter" and re.search(r"\b(compare|comparison)\b", text):
        return PAGE_GUIDES[role]["Compare Properties"]
    if role == "renter" and re.search(r"\b(pay|payment|rent due)\b", text):
        return PAGE_GUIDES[role]["Pay Rent"]
    if role == "renter" and re.search(r"\b(search|find|filter|look)\w*\b.*\b(property|properties|rent|home|house)\b", text):
        return PAGE_GUIDES[role]["Search Properties"]

    role_pages = PAGE_GUIDES[role]
    if page in role_pages:
        return role_pages[page]
    return role_pages.get("Dashboard", next(iter(role_pages.values())))


def send_message(uid: str, role: str, question: str, page: str = "") -> tuple[str, str]:
    response = static_answer(role, page, question)
    repo = SQLiteRepository()
    repo.add_chatbot_message(uid, "user", question)
    repo.add_chatbot_message(uid, "assistant", response, MODE)
    return response, MODE
