export const EMAIL_ACTION_DEFAULTS = {
  email_customer: {
    key: "inquiry_confirmation",
    subject_template: "We've received your enquiry – {{business_name}}",
    body_template: [
      "Hello {{contact.first_name|there}},",
      "Thank you for contacting {{business_name}}. This email confirms that we have received your enquiry.",
      "{{customer_recap}}",
      "Next step: {{next_step}}",
      "A member of our team will get back to you as soon as possible. If anything changes in the meantime, simply reply to this email.",
      "Kind regards,\n{{business_name}}",
    ].join("\n\n"),
  },
  notify_staff: {
    key: "staff_new_enquiry",
    subject_template: "New enquiry from {{contact.name|a customer}}",
    body_template: [
      "{{summary}}",
      "Next step: {{next_step}}\nName: {{contact.name}}\nPhone: {{contact.phone}}\nEmail: {{contact.email}}\nChannel: {{channel}}",
      "{{intake_table}}",
      "{{conversation.link}}",
    ].join("\n\n"),
  },
} as const;
