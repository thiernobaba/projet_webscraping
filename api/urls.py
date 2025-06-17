from django.urls import path
from .views import ImportJobsAPIView

urlpatterns = [
    path('import/', ImportJobsAPIView.as_view(), name='import-jobs'),
]