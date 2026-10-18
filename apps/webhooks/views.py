from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.tickets.permissions import staff_view

from .forms import WebhookEndpointForm
from .models import WebhookEndpoint


@staff_view
def endpoint_list(request):
    endpoints = WebhookEndpoint.objects.select_related("source_application").order_by(
        "-created_at"
    )
    return render(request, "webhooks/endpoint_list.html", {"endpoints": endpoints})


@staff_view
def endpoint_create(request):
    if request.method == "POST":
        form = WebhookEndpointForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Webhook endpoint created.")
            return redirect("webhooks:endpoint_list")
    else:
        form = WebhookEndpointForm()

    return render(request, "webhooks/endpoint_form.html", {"form": form})


@staff_view
def endpoint_edit(request, pk):
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)

    if request.method == "POST":
        form = WebhookEndpointForm(request.POST, instance=endpoint)
        if form.is_valid():
            form.save()
            messages.success(request, "Webhook endpoint updated.")
            return redirect("webhooks:endpoint_list")
    else:
        form = WebhookEndpointForm(instance=endpoint)

    return render(
        request, "webhooks/endpoint_form.html", {"form": form, "endpoint": endpoint}
    )


@staff_view
def endpoint_delete(request, pk):
    endpoint = get_object_or_404(WebhookEndpoint, pk=pk)

    if request.method == "POST":
        endpoint.delete()
        messages.success(request, "Webhook endpoint deleted.")
        return redirect("webhooks:endpoint_list")

    return redirect("webhooks:endpoint_list")


@staff_view
def delivery_list(request, pk):
    endpoint = get_object_or_404(
        WebhookEndpoint.objects.select_related("source_application"), pk=pk
    )
    deliveries = endpoint.deliveries.order_by("-created_at")

    paginator = Paginator(deliveries, 50)
    page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "webhooks/delivery_list.html",
        {"endpoint": endpoint, "page": page},
    )
