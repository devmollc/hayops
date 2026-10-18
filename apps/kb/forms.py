from django import forms
from django.urls import reverse_lazy

from .models import Article, Category


class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["title", "slug", "body", "category", "source_application", "is_published"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "rows": 24,
                    "class": "form-input font-mono",
                    "placeholder": "Write article content in Markdown...",
                    # Live HTML preview -- the editor writes Markdown but the article
                    # is read as HTML, so show what the reader will actually get.
                    "hx-post": reverse_lazy("kb:article_preview"),
                    "hx-trigger": "keyup changed delay:400ms, load",
                    "hx-target": "#kb-preview",
                }
            ),
            "slug": forms.TextInput(attrs={"placeholder": "auto-generated-from-title"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.all()
