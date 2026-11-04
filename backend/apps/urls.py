from django.urls import path

from . import views

urlpatterns = [
    path("options/", views.OptionsView.as_view()),
    path("sessions/", views.SessionListView.as_view()),
    path("sessions/<uuid:session_id>/messages/", views.MessageListView.as_view()),
    path("sessions/<uuid:session_id>/messages/stream/", views.StreamMessageView.as_view()),
    path("sessions/<uuid:session_id>/files/", views.FileUploadView.as_view()),
    path("sessions/<uuid:session_id>/workspace/", views.WorkspaceFileListView.as_view()),
    path("sessions/<uuid:session_id>/workspace/file/", views.WorkspaceFileContentView.as_view()),
    path("sessions/<uuid:session_id>/plugins/", views.PluginListView.as_view()),
    path("sessions/<uuid:session_id>/plugins/<str:name>/", views.PluginDetailView.as_view()),
    path("sessions/<uuid:session_id>/workflows/", views.WorkflowListView.as_view()),
    path("sessions/<uuid:session_id>/workflows/<str:name>/", views.WorkflowDetailView.as_view()),
    path("sessions/<uuid:session_id>/eureka-runs/<int:run_id>/", views.EurekaRunView.as_view()),
    path("eureka/runs/<int:run_id>/stages/<str:stage_key>/logs/", views.WebhookLogView.as_view()),
    path("sessions/<uuid:session_id>/stream/", views.StreamView.as_view()),
]
