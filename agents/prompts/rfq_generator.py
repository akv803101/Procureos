"""RFQ generator prompt — verbatim from prompts.md (Prompt 5)."""

RFQ_GENERATOR_PROMPT = """
Generate a professional WhatsApp RFQ message to an Indian B2B vendor.
Write in clear, polite English. Be concise — WhatsApp messages should be brief.

Vendor name: {vendor_name}
Category: {category_display}
Quantity / Scope: {quantity_display}
Location / Delivery city: {location}
Budget: ₹{budget_display}
GST invoice required: {gst_required}
Timeline: {urgency_display}
Reference code: {ref_code}
Is first contact: {is_first_contact}

Generate a WhatsApp message (max 250 words) that:
1. Introduces ProcureOS as a corporate procurement platform briefly
2. States the requirement clearly with all details above
3. Asks vendor to reply with: price, delivery timeline, GST availability
4. Includes the REF code so we can match their reply to this request
5. Offers PASS option to decline politely
6. If is_first_contact is true: add one-line consent footer at the end

Return ONLY the message text. No JSON, no explanation, just the message.
"""
