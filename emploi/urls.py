# urls.py
from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    # Dashboard principal
    path('', views.dashboard_home, name='home'),
    
    # Listes et filtres
    path('offres/', views.offres_list, name='offres_list'),
    path('offres/<int:offre_id>/', views.offre_detail, name='offre_detail'),
    path('entreprises/<int:entreprise_id>/', views.entreprise_detail, name='entreprise_detail'),
    
    # Analytics
    path('analytics/', views.analytics_view, name='analytics'),
    
    # API pour les graphiques
    path('api/charts/', views.api_charts_data, name='api_charts'),
    path('offres/export-csv/', views.export_csv, name='export_csv'),
    path('recherche-avancee/', views.advanced_search, name='advanced_search'),
]
