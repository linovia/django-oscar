import stripe
from django.conf import settings
from oscar.apps.order import processing
from oscar.apps.payment import exceptions

from .models import PaymentEventType


class EventHandler(processing.EventHandler):

    def handle_shipping_event(self, order, event_type, lines,
                              line_quantities, **kwargs):
        self.validate_shipping_event(
            order, event_type, lines, line_quantities, **kwargs)

        payment_event = None
        if event_type.name == 'Shipped':
            # Take payment for order lines
            self.take_payment_for_lines(
                order, lines, line_quantities)
            self.consume_stock_allocations(
                order, lines, line_quantities)

        shipping_event = self.create_shipping_event(
            order, event_type, lines, line_quantities,
            reference=kwargs.get('reference', None))
        if payment_event:
            shipping_event.payment_events.add(payment_event)

    def take_payment_for_lines(self, order, lines, line_quantities):
        settle, __ = PaymentEventType.objects.get_or_create(
            name="Settle")
        amount = self.calculate_amount_to_settle(
            settle, order, lines, line_quantities)

        # Perform the real payment
        stripe_payment = order.sources.get(source_type__name='Stripe')
        try:
            charge = stripe.Charge.create(
                amount=int(amount*100), # amount in cents, again
                currency="eur",
                card=stripe_payment.reference,
                description="payinguser@example.com",
                api_key=settings.STRIPE_API_KEY
            )
        except (stripe.CardError, exceptions.PaymentError), e:
            self.create_note(order, "Attempt to settle %.2f failed: %s" % (
                amount, e))
            raise

        # Record message
        msg = "Payment of %.2f settled using reference '%s' from initial transaction"
        msg = msg % (amount, charge.id)
        self.create_note(order, msg)

        # Update order source
        stripe_payment.debit(amount, reference=charge.id)

        # Create payment event
        return self.create_payment_event(
            order, settle, amount, lines, line_quantities,
            reference=charge.id)

    def calculate_amount_to_settle(
            self, event_type, order, lines, line_quantities):
        amt = self.calculate_payment_event_subtotal(
            event_type, lines, line_quantities)
        num_payments = order.payment_events.filter(
            event_type=event_type).count()
        if num_payments == 0:
            # Include shipping charge in first payment
            amt += order.shipping_incl_tax
        return amt
