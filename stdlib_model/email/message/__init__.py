# the model of email.message: Message is named only, for the isinstance() in httplib2's Response, as no model
# makes one; compiled for a model checker, it's a stand-in, so the models don't import email, which imports
# socket, random and binascii

import email.message

from PyMagic import named

Message = named(email.message.Message)
