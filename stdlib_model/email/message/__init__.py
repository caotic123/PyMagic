# the model of email.message: Message is named only, for the isinstance() in httplib2's Response, as no model
# makes one

import email.message

Message = email.message.Message  # named only
