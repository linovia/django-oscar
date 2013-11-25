from django.contrib import messages
from django import http
from django.core.urlresolvers import reverse
from django.utils.translation import ugettext_lazy as _

from oscar.apps.checkout import views
from oscar.apps.payment.models import SourceType
from oscar.core.loading import get_classes

from apps.stripe.forms import StripeTokenForm


PaymentError = get_classes(
    'payment.exceptions', ['PaymentError'])

# Customise the core PaymentDetailsView to integrate Stripe
class PaymentDetailsView(views.PaymentDetailsView):

    def get_context_data(self, **kwargs):
        # Add bankcard form to the template context
        if 'stripe_form' not in kwargs:
            kwargs['stripe_form'] = StripeTokenForm()
        ctx =  super(PaymentDetailsView, self).get_context_data(**kwargs)
        return ctx

    def post(self, request, *args, **kwargs):
        if request.POST.get('action', '') == 'place_order':
            return self.do_place_order(request)

        # Check StripToken form is valid
        stripe_form = StripeTokenForm(request.POST)
        if not stripe_form.is_valid():
            # Stripe form invalid, re-render the payment details template
            self.preview = False
            ctx = self.get_context_data(**kwargs)
            ctx['stripe_form'] = stripe_form
            return self.render_to_response(ctx)

        # Render preview page (with completed stripe form hidden).
        # Note, we don't write the stripe details to the session or DB
        # as a security precaution.
        return self.render_preview(request, stripe_form=stripe_form)

    def do_place_order(self, request):
        # Double-check the stripe data is still valid
        stripe_form = StripeTokenForm(request.POST)
        if not stripe_form.is_valid():
            # Must be tampering - we don't need to be that friendly with our
            # error message.
            messages.error(request, _("Invalid submission"))
            return http.HttpResponseRedirect(
                reverse('checkout:payment-details'))

        submission = self.build_submission(stripe_form)
        return self.submit(**submission)

    def build_submission(self, stripe_form, **kwargs):
        # Modify the default submission dict with the stripe instance
        submission = super(PaymentDetailsView, self).build_submission()
        if stripe_form.is_valid():
            submission['payment_kwargs']['stripe'] = stripe_form.cleaned_data['stripeToken']
        return submission

    def handle_payment(self, order_number, total, **kwargs):
        # Make request to Stripe - if there any problems (eg stripe
        # not valid / request refused by stripe) then an exception would be
        # raised and handled by the parent PaymentDetail view)

        try:
            stripe_token = kwargs['stripe']
        except KeyError:
            raise PaymentError('No token found.')

        # Request was successful - record the "payment source".  As this
        # request was a 'pre-auth', we set the 'amount_allocated' - if we had
        # performed an 'auth' request, then we would set 'amount_debited'.
        source_type, _ = SourceType.objects.get_or_create(name='Stripe')
        source = source_type.sources.model(
            source_type=source_type,
            currency=total.currency,
            amount_allocated=total.incl_tax,
            reference=stripe_token)
        self.add_payment_source(source)

        # Also record payment event
        self.add_payment_event(
            'pre-auth', total.incl_tax, reference=stripe_token)
