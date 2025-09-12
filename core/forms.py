from django import forms

class SendForm(forms.Form):
    recipient_email = forms.EmailField(label="Email do Destinatário", help_text="Email do usuário para quem enviar.")
    amount = forms.CharField(label="Quantia", help_text="Valor a enviar (mínimo 0.01).")

class WithdrawForm(forms.Form):
    method = forms.ChoiceField(choices=[('PIX', 'PIX')], label="Método de Saque", initial='PIX', widget=forms.Select(attrs={'style': 'border: 1px solid var(--line);'}))
    pix_key = forms.CharField(max_length=100, label="Chave PIX")
    amount = forms.CharField(label="Quantia", help_text="Valor a sacar (mínimo 0.01).")
    pin = forms.CharField(max_length=4, min_length=4, label="PIN de Saque (4 dígitos)")
    
class UploadCtwaCatalogForm(forms.Form):
    file = forms.FileField(
        label="CSV exportado do Ads Manager",
        help_text="Colunas: Ad ID, Ad Name, Ad Set ID, Ad Set Name, Campaign ID, Campaign Name",
    )
