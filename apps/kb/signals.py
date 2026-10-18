from django.contrib.postgres.search import SearchVector
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Article


@receiver(post_save, sender=Article)
def update_article_search_vector(sender, instance, **kwargs):
    Article.objects.filter(pk=instance.pk).update(
        search_vector=SearchVector("title", weight="A") + SearchVector("body", weight="B")
    )
