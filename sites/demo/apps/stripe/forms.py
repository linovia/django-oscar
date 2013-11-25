
from django import forms


class StripeTokenForm(forms.Form):
	stripeEmail = forms.CharField()
	stripeToken = forms.CharField()
