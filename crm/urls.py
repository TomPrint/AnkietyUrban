from django.urls import path

from .views import customer_create, customer_delete, customer_detail, customer_edit, customer_list

urlpatterns = [
    path("portal/customers/", customer_list, name="portal-customers"),
    path("portal/customers/<int:customer_id>/", customer_detail, name="portal-customer-detail"),
    path("portal/customers/new/", customer_create, name="portal-customer-create"),
    path("portal/customers/<int:customer_id>/edit/", customer_edit, name="portal-customer-edit"),
    path("portal/customers/<int:customer_id>/delete/", customer_delete, name="portal-customer-delete"),
    path("management/customers/", customer_list, name="management-customers"),
    path("management/customers/<int:customer_id>/", customer_detail, name="management-customer-detail"),
    path("management/customers/new/", customer_create, name="management-customer-create"),
    path("management/customers/<int:customer_id>/edit/", customer_edit, name="management-customer-edit"),
    path("management/customers/<int:customer_id>/delete/", customer_delete, name="management-customer-delete"),
]

