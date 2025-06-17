# views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db.models import Count, Q, Avg, Max, Min
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import json
import csv
from django.http import HttpResponse

from .models import OffreEmploi, Entreprise, Region, StatistiquesGlobales

def dashboard_home(request):
    """Vue principale du dashboard"""
    # Statistiques générales
    total_offres = OffreEmploi.objects.filter(is_active=True).count()
    total_entreprises = Entreprise.objects.count()
    
    # Offres récentes (7 derniers jours)
    date_limite = timezone.now() - timedelta(days=7)
    offres_recentes = OffreEmploi.objects.filter(
        date_publication__gte=date_limite,
        is_active=True
    ).count()
    
    # Top 5 des entreprises qui recrutent le plus
    top_entreprises = Entreprise.objects.annotate(
        nb_offres=Count('offres', filter=Q(offres__is_active=True))
    ).order_by('-nb_offres')[:5]
    
    # Répartition par type de contrat
    contrats_stats = OffreEmploi.objects.filter(is_active=True).values('type_contrat').annotate(
        count=Count('entreprise_id')
    ).order_by('-count')
    
    # Répartition par région
    regions_stats = OffreEmploi.objects.filter(is_active=True, region__isnull=False).values(
        'region__nom'
    ).annotate(count=Count('id')).order_by('-count')[:10]
    
    context = {
        'total_offres': total_offres,
        'total_entreprises': total_entreprises,
        'offres_recentes': offres_recentes,
        'top_entreprises': top_entreprises,
        'contrats_stats': contrats_stats,
        'regions_stats': regions_stats,
        'page_title': 'Dashboard Emploi Sénégal'
    }
    
    return render(request, 'dashboard/home.html', context)

def offres_list(request):
    """Liste des offres avec filtres"""
    offres = OffreEmploi.objects.filter(is_active=True).select_related(
        'entreprise', 'region'
    ).order_by('-date_publication')
    
    # Filtres
    search_query = request.GET.get('search', '')
    region_filter = request.GET.get('region', '')
    contrat_filter = request.GET.get('contrat', '')
    secteur_filter = request.GET.get('secteur', '')
    
    if search_query:
        offres = offres.filter(
            Q(titre_poste__icontains=search_query) |
            Q(entreprise__nom__icontains=search_query) |
            Q(description__icontains=search_query)
        )
    
    if region_filter:
        offres = offres.filter(region__nom=region_filter)
    
    if contrat_filter:
        offres = offres.filter(type_contrat=contrat_filter)
    
    if secteur_filter:
        offres = offres.filter(secteur_activite=secteur_filter)
    
    # Pagination (25 offres par page)
    from django.core.paginator import Paginator
    paginator = Paginator(offres, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Données pour les filtres
    regions = Region.objects.annotate(
        nb_offres=Count('offreemploi', filter=Q(offreemploi__is_active=True))
    ).filter(nb_offres__gt=0).order_by('nom')
    
    secteurs = OffreEmploi.objects.filter(
        is_active=True, 
        secteur_activite__isnull=False
    ).values_list('secteur_activite', flat=True).distinct()
    
    context = {
        'page_obj': page_obj,
        'regions': regions,
        'secteurs': secteurs,
        'current_filters': {
            'search': search_query,
            'region': region_filter,
            'contrat': contrat_filter,
            'secteur': secteur_filter,
        },
        'type_contrat_choices': OffreEmploi.TYPE_CONTRAT_CHOICES,
        'page_title': 'Toutes les offres d\'emploi'
    }
    
    return render(request, 'dashboard/offres_list.html', context)

def analytics_view(request):
    """Vue pour les analytics avancées"""
    # Evolution mensuelle des offres
    evolution_mensuelle = OffreEmploi.objects.filter(
        is_active=True,
        date_publication__gte=timezone.now() - timedelta(days=365)
    ).annotate(
        mois=TruncMonth('date_publication')
    ).values('mois').annotate(
        count=Count('id')
    ).order_by('mois')
    
    # Top 10 des compétences les plus demandées
    competences_list = []
    for offre in OffreEmploi.objects.filter(is_active=True, competences__isnull=False):
        if offre.competences:
            competences_list.extend([c.strip().lower() for c in offre.competences.split(',')])
    
    competences_counter = Counter(competences_list)
    top_competences = competences_counter.most_common(15)
    
    # Analyse salariale
    offres_avec_salaire = OffreEmploi.objects.filter(
        is_active=True,
        salaire_min__isnull=False
    )
    
    # Calcul correct de la médiane
    def get_median_salary(queryset):
        count = queryset.count()
        if not count:
            return None
        ordered = queryset.order_by('salaire_min')
        if count % 2 == 0:
            mid_high = ordered[count//2].salaire_min
            mid_low = ordered[count//2 - 1].salaire_min
            return (mid_high + mid_low) / 2
        else:
            return ordered[count//2].salaire_min
    
    salaire_stats = {
        'moyenne': offres_avec_salaire.aggregate(Avg('salaire_min'))['salaire_min__avg'],
        'median': get_median_salary(offres_avec_salaire),
        'min': offres_avec_salaire.aggregate(Min('salaire_min'))['salaire_min__min'],
        'max': offres_avec_salaire.aggregate(Max('salaire_min'))['salaire_min__max'],
    }
    
    # Répartition par niveau d'expérience
    experience_stats = OffreEmploi.objects.filter(
        is_active=True,
        niveau_experience__isnull=False
    ).values('niveau_experience').annotate(
        count=Count('id')
    ).order_by('-count')
    
    context = {
        'evolution_mensuelle': list(evolution_mensuelle),
        'top_competences': top_competences,
        'salaire_stats': salaire_stats,
        'experience_stats': experience_stats,
        'page_title': 'Analytics & Insights'
    }
    
    return render(request, 'dashboard/analytics.html', context)

def api_charts_data(request):
    """API pour les données des graphiques"""
    chart_type = request.GET.get('type', 'contrats')
    
    if chart_type == 'contrats':
        data = list(OffreEmploi.objects.filter(is_active=True).values('type_contrat').annotate(
            count=Count('id')
        ))
    
    elif chart_type == 'regions':
        data = list(OffreEmploi.objects.filter(
            is_active=True, 
            region__isnull=False
        ).values('region__nom').annotate(
            count=Count('id')
        ).order_by('-count')[:10])
    
    elif chart_type == 'evolution':
        data = list(OffreEmploi.objects.filter(
            date_publication__gte=timezone.now() - timedelta(days=180)
        ).annotate(
            semaine=TruncWeek('date_publication')
        ).values('semaine').annotate(
            count=Count('id')
        ).order_by('semaine'))
    
    elif chart_type == 'secteurs':
        data = list(OffreEmploi.objects.filter(
            is_active=True,
            secteur_activite__isnull=False
        ).values('secteur_activite').annotate(
            count=Count('id')
        ).order_by('-count')[:10])
    
    else:
        data = []
    
    return JsonResponse({'data': data})

def offre_detail(request, offre_id):
    """Détail d'une offre"""
    offre = get_object_or_404(OffreEmploi, id=offre_id, is_active=True)
    
    # Incrémenter le compteur de vues
    offre.vues += 1
    offre.save(update_fields=['vues'])
    
    # Offres similaires
    offres_similaires = OffreEmploi.objects.filter(
        is_active=True,
        secteur_activite=offre.secteur_activite
    ).exclude(id=offre.id)[:5]
    
    context = {
        'offre': offre,
        'offres_similaires': offres_similaires,
        'page_title': f'{offre.titre_poste} - {offre.entreprise.nom}'
    }
    
    return render(request, 'dashboard/offre_detail.html', context)

def entreprise_detail(request, entreprise_id):
    """Profil d'une entreprise avec toutes les statistiques"""
    entreprise = get_object_or_404(Entreprise, id=entreprise_id)
    
    # Récupérer toutes les offres de l'entreprise
    offres_queryset = OffreEmploi.objects.filter(
        entreprise=entreprise,
        is_active=True
    ).select_related('region').order_by('-date_publication')
    
    # Pagination des offres
    paginator = Paginator(offres_queryset, 10)  # 10 offres par page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Statistiques de base
    total_offres = offres_queryset.count()
    total_vues = offres_queryset.aggregate(Sum('vues'))['vues__sum'] or 0
    derniere_offre = offres_queryset.first()
    
    # Secteurs d'activité avec comptage
    secteurs_offres = offres_queryset.filter(
        secteur_activite__isnull=False
    ).values('secteur_activite').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Renommer pour correspondre au template
    secteurs_offres = [
        {'nom': secteur['secteur_activite'], 'count': secteur['count']} 
        for secteur in secteurs_offres
    ]
    
    # Types de contrats avec comptage
    types_contrats = offres_queryset.values('type_contrat').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Renommer pour correspondre au template
    types_contrats = [
        {'nom': dict(OffreEmploi.TYPE_CONTRAT_CHOICES)[contrat['type_contrat']], 'count': contrat['count']} 
        for contrat in types_contrats
    ]
    
    # Ajouter les propriétés calculées à l'objet entreprise
    entreprise.offres_count = total_offres
    entreprise.total_vues = total_vues
    entreprise.derniere_offre = derniere_offre.date_publication if derniere_offre else None
    entreprise.secteurs_offres = secteurs_offres
    entreprise.types_contrats = types_contrats
    
    # Gestion du tri
    sort_param = request.GET.get('sort', 'recent')
    if sort_param == 'anciennes':
        page_obj.object_list = page_obj.object_list.order_by('date_publication')
    elif sort_param == 'titre':
        page_obj.object_list = page_obj.object_list.order_by('titre_poste')
    # 'recent' est déjà l'ordre par défaut
    
    context = {
        'entreprise': entreprise,
        'page_obj': page_obj,
        'page_title': f'Profil de {entreprise.nom}'
    }
    
    return render(request, 'dashboard/entreprise_detail.html', context)

def export_csv(request):
    """Exporte les offres d'emploi en CSV."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="offres_emploi.csv"'

    # Écriture des en-têtes du CSV
    writer = csv.writer(response)
    writer.writerow([
        'Titre du poste', 
        'Entreprise', 
        'Lieu', 
        'Date publication',
        'Type de contrat',
        'Secteur',
        'Expérience requise',
        'URL'
    ])

    # Récupération des données avec les mêmes filtres que la vue offres_list
    offres = OffreEmploi.objects.filter(is_active=True).select_related('entreprise', 'region')
    
    # Appliquer les mêmes filtres que dans offres_list si besoin
    search_query = request.GET.get('search', '')
    if search_query:
        offres = offres.filter(
            Q(titre_poste__icontains=search_query) |
            Q(entreprise__nom__icontains=search_query) |
            Q(description__icontains=search_query))
    
    # Écriture des données
    for offre in offres:
        writer.writerow([
            offre.titre_poste,
            offre.entreprise.nom if offre.entreprise else '',
            offre.lieu,
            offre.date_publication.strftime('%d/%m/%Y') if offre.date_publication else '',
            offre.get_type_contrat_display(),
            offre.secteur_activite,
            offre.niveau_experience,
            request.build_absolute_uri(offre.get_absolute_url()) if hasattr(offre, 'get_absolute_url') else ''
        ])

    return response

def advanced_search(request):
    """Cette vue peut simplement rediriger vers offres_list"""
    return offres_list(request)