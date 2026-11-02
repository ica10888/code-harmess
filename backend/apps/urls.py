from django.urls import path

from . import views

urlpatterns = [
    path("options/", views.OptionsView.as_view()),
    path("sessions/", views.SessionListView.as_view()),
    path("sessions/<uuid:session_id>/messages/", views.MessageListView.as_view()),
    path("sessions/<uuid:session_id>/messages/stream/", views.StreamMessageView.as_view()),
    path("sessions/<uuid:session_id>/files/", views.FileUploadView.as_view()),
    path("sessions/<uuid:session_id>/stream/", views.StreamView.as_view()),
]
