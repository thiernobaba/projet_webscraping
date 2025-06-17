import csv
import json
import io
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.conf import settings
import logging

from emploi.models import OffreEmploi, Entreprise, Region

logger = logging.getLogger(__name__)

@method_decorator(csrf_exempt, name='dispatch')
class ImportJobsAPIView(View):
    """
    API pour importer des offres d'emploi
    Support: CSV upload, JSON direct, URL de fichier CSV
    """
    
    def dispatch(self, request, *args, **kwargs):
        """Ajouter les headers CORS"""
        response = super().dispatch(request, *args, **kwargs)
        
        # Headers CORS
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response['Access-Control-Allow-Credentials'] = 'true'
        
        return response

    def options(self, request, *args, **kwargs):
        """Gérer les requêtes OPTIONS (preflight CORS)"""
        response = JsonResponse({})
        response['Access-Control-Allow-Origin'] = '*'
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return response
    
    def post(self, request):
        try:
            content_type = request.content_type
            logger.info(f"Requête reçue avec content-type: {content_type}")
            
            if 'multipart/form-data' in content_type:
                return self.handle_file_upload(request)
            elif 'application/json' in content_type:
                return self.handle_json_data(request)
            else:
                return JsonResponse({
                    'success': False,
                    'error': 'Content-Type non supporté. Utilisez multipart/form-data ou application/json'
                }, status=400)
                
        except Exception as e:
            logger.error(f"Erreur dans l'API d'import: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Erreur serveur: {str(e)}'
            }, status=500)

    def handle_file_upload(self, request):
        """Gère l'upload de fichier CSV"""
        logger.info("Traitement d'un upload de fichier CSV")
        
        if 'csv_file' not in request.FILES:
            return JsonResponse({
                'success': False,
                'error': 'Aucun fichier CSV fourni'
            }, status=400)
        
        csv_file = request.FILES['csv_file']
        options = self.get_options_from_request(request)
        logger.info(f"Options reçues: {options}")
        
        # Lire le contenu du fichier
        try:
            content = csv_file.read().decode('utf-8')
        except UnicodeDecodeError:
            try:
                csv_file.seek(0)
                content = csv_file.read().decode('iso-8859-1')
            except UnicodeDecodeError:
                return JsonResponse({
                    'success': False,
                    'error': 'Impossible de décoder le fichier. Utilisez UTF-8 ou ISO-8859-1'
                }, status=400)
        
        return self.process_csv_content(content, options)

    def handle_json_data(self, request):
        """Gère les données JSON directes"""
        try:
            data = json.loads(request.body)
            logger.info(f"Données JSON reçues: {list(data.keys())}")
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'JSON invalide'
            }, status=400)
        
        if 'csv_url' in data:
            return self.handle_csv_url(data['csv_url'], data.get('options', {}))
        elif 'jobs' in data:
            return self.handle_job_list(data['jobs'], data.get('options', {}))
        elif 'csv_content' in data:
            return self.process_csv_content(data['csv_content'], data.get('options', {}))
        else:
            return JsonResponse({
                'success': False,
                'error': 'Format JSON invalide. Utilisez "jobs", "csv_content" ou "csv_url"'
            }, status=400)

    def handle_csv_url(self, csv_url, options):
        """Télécharge et traite un fichier CSV depuis une URL"""
        import requests
        try:
            response = requests.get(csv_url, timeout=30)
            response.raise_for_status()
            content = response.text
            return self.process_csv_content(content, options)
        except requests.RequestException as e:
            return JsonResponse({
                'success': False,
                'error': f'Erreur lors du téléchargement: {str(e)}'
            }, status=400)

    def handle_job_list(self, jobs, options):
        """Traite une liste d'offres d'emploi en JSON"""
        logger.info(f"Traitement de {len(jobs)} offres avec options: {options}")
        
        stats = {
            'total': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'missing_fields': 0
        }
        
        errors = []
        
        for job_data in jobs:
            stats['total'] += 1
            logger.info(f"Traitement offre {stats['total']}: {job_data.get('titre_poste', 'Sans titre')}")
            
            try:
                with transaction.atomic():
                    result = self.process_single_job(job_data, options)
                    stats[result['action']] += 1
                    stats['missing_fields'] += result.get('missing_count', 0)
                    logger.info(f"Offre {stats['total']} - Action: {result['action']}")
            except Exception as e:
                stats['errors'] += 1
                error_msg = str(e)
                logger.error(f"Erreur offre {stats['total']}: {error_msg}")
                errors.append({
                    'job': stats['total'],
                    'error': error_msg,
                    'job_data': job_data
                })
                if not options.get('skip_invalid', False):
                    break
        
        logger.info(f"Résultats finaux: {stats}")
        return JsonResponse({
            'success': True,
            'stats': stats,
            'errors': errors[:10]  # Limiter les erreurs affichées
        })

    def process_csv_content(self, content, options):
        """Traite le contenu CSV"""
        logger.info(f"Traitement CSV avec options: {options}")
        
        stats = {
            'total': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'missing_fields': 0
        }
        
        errors = []
        
        try:
            # Détecter le délimiteur
            delimiter = options.get('delimiter', ',')
            if delimiter == 'auto':
                sniffer = csv.Sniffer()
                delimiter = sniffer.sniff(content[:1024]).delimiter
            
            logger.info(f"Délimiteur utilisé: '{delimiter}'")
            reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
            
            # Afficher les colonnes détectées
            fieldnames = reader.fieldnames
            logger.info(f"Colonnes détectées: {fieldnames}")
            
            for row in reader:
                stats['total'] += 1
                logger.info(f"Ligne {stats['total']}: {dict(row)}")
                
                try:
                    with transaction.atomic():
                        result = self.process_single_job(dict(row), options)
                        stats[result['action']] += 1
                        stats['missing_fields'] += result.get('missing_count', 0)
                        logger.info(f"Ligne {stats['total']} - Action: {result['action']}")
                except Exception as e:
                    stats['errors'] += 1
                    error_msg = str(e)
                    logger.error(f"Erreur ligne {stats['total']}: {error_msg}")
                    errors.append({
                        'line': stats['total'],
                        'error': error_msg,
                        'data': dict(row)
                    })
                    if not options.get('skip_invalid', False):
                        break
            
            logger.info(f"Résultats CSV finaux: {stats}")
            return JsonResponse({
                'success': True,
                'stats': stats,
                'errors': errors[:10]
            })
            
        except Exception as e:
            logger.error(f"Erreur traitement CSV: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Erreur lors du traitement CSV: {str(e)}'
            }, status=400)

    def process_single_job(self, job_data, options):
        """Traite une seule offre d'emploi en ignorant uniquement les doublons exacts"""
        offre_data, missing_count = self.prepare_offre_data(job_data)
        
        # Créer ou récupérer l'entreprise
        entreprise, _ = Entreprise.objects.get_or_create(
            nom=offre_data['entreprise_nom'],
            defaults={
                'tagline': offre_data.get('entreprise_tagline', ''),
                'site_web': offre_data.get('entreprise_site_web', ''),
                'secteur': offre_data.get('entreprise_secteur', '')
            }
        )

        # Créer ou récupérer la région
        region = None
        if offre_data.get('region') and offre_data['region'] != 'NA':
            region, _ = Region.objects.get_or_create(
                nom=offre_data['region'],
                defaults={'code': offre_data.get('region_code', '')}
            )

        # Vérifier si une offre identique existe déjà
        create_fields = self.get_create_fields(offre_data, entreprise, region)
        
        # Requête pour trouver des doublons exacts
        duplicate_query = OffreEmploi.objects.filter(
            titre_poste=create_fields['titre_poste'],
            entreprise=entreprise,
            lieu=create_fields['lieu'],
            type_contrat=create_fields['type_contrat'],
            url_offre=create_fields['url_offre'],
            description=create_fields['description'],
            salaire_min=create_fields.get('salaire_min'),
            salaire_max=create_fields.get('salaire_max'),
            niveau_experience=create_fields.get('niveau_experience'),
            secteur_activite=create_fields.get('secteur_activite'),
            competences=create_fields.get('competences'),
            source_site=create_fields.get('source_site')
        )
        
        if region:
            duplicate_query = duplicate_query.filter(region=region)
        else:
            duplicate_query = duplicate_query.filter(region__isnull=True)

        if duplicate_query.exists():
            return {'action': 'skipped', 'missing_count': missing_count, 'reason': 'duplicate'}
        
        # Créer une nouvelle offre si aucun doublon exact trouvé
        offre = OffreEmploi(**create_fields)
        offre.full_clean()
        offre.save()
        
        return {'action': 'created', 'missing_count': missing_count}

    def prepare_offre_data(self, row):
        """Prépare les données d'une offre d'emploi"""
        missing_count = 0
        
        def get_field(field, default='NA'):
            nonlocal missing_count
            value = str(row.get(field, '')).strip()
            if not value or value.lower() in ['null', 'none', '']:
                missing_count += 1
                return default
            return value

        # Traitement de la date
        date_publication = self.parse_date(get_field('date_publication', None))
        
        # Traitement du salaire
        salaire_min, salaire_max = None, None
        salaire = get_field('salaire', None)
        if salaire and salaire != 'NA':
            try:
                if '-' in salaire:
                    salaire_parts = salaire.split('-')
                    salaire_min = int(salaire_parts[0].strip())
                    salaire_max = int(salaire_parts[1].strip())
                else:
                    salaire_min = int(salaire.strip())
            except (ValueError, TypeError):
                pass

        prepared_data = {
            'titre_poste': get_field('titre_poste'),
            'entreprise_nom': get_field('entreprise'),
            'entreprise_tagline': get_field('tagline_entreprise', ''),
            'entreprise_site_web': get_field('entreprise_site_web', ''),
            'entreprise_secteur': get_field('secteur', ''),
            'lieu': get_field('lieu'),
            'region': get_field('region', ''),
            'region_code': get_field('region_code', ''),
            'type_contrat': get_field('type_contrat', 'INDEFINI').upper(),
            'date_publication': date_publication or timezone.now(),
            'url_offre': get_field('url_offre'),
            'description': get_field('description', ''),
            'salaire_min': salaire_min,
            'salaire_max': salaire_max,
            'niveau_experience': get_field('niveau_experience', '').upper(),
            'secteur_activite': get_field('secteur_activite', ''),
            'competences': get_field('competences', ''),
            'source_site': get_field('source_site', 'api')
        }
        
        logger.info(f"Données préparées avec {missing_count} champs manquants")
        return prepared_data, missing_count

    def get_update_fields(self, offre_data, entreprise, region):
        """Champs pour la mise à jour"""
        return {
            'titre_poste': offre_data['titre_poste'],
            'entreprise': entreprise,
            'lieu': offre_data['lieu'],
            'region': region,
            'type_contrat': offre_data['type_contrat'],
            'date_publication': offre_data['date_publication'],
            'description': offre_data['description'],
            'salaire_min': offre_data.get('salaire_min'),
            'salaire_max': offre_data.get('salaire_max'),
            'niveau_experience': offre_data.get('niveau_experience'),
            'secteur_activite': offre_data.get('secteur_activite'),
            'competences': offre_data.get('competences'),
            'source_site': offre_data.get('source_site')
        }

    def get_create_fields(self, offre_data, entreprise, region):
        """Champs pour la création"""
        fields = self.get_update_fields(offre_data, entreprise, region)
        fields['url_offre'] = offre_data['url_offre']
        return fields

    def parse_date(self, date_str):
        """Parse différents formats de date"""
        if not date_str or date_str == 'NA':
            return None
            
        formats = [
            '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', 
            '%d-%m-%Y', '%Y/%m/%d', '%Y-%m-%d %H:%M:%S'
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
        return None

    def get_options_from_request(self, request):
        """Extrait les options de la requête"""
        # Pour les requêtes multipart/form-data
        update = request.POST.get('update')
        skip_invalid = request.POST.get('skip_invalid')
        
        # Gérer les différents formats de valeurs booléennes
        options = {
            'update': update in ['true', 'True', '1', 'on', True],
            'skip_invalid': skip_invalid in ['true', 'True', '1', 'on', True],
            'delimiter': request.POST.get('delimiter', ',')
        }
        
        logger.info(f"Options extraites de la requête: {options}")
        return options

    def get(self, request):
        """Documentation de l'API"""
        return JsonResponse({
            'api_name': 'Job Import API',
            'version': '1.0',
            'status': 'OK',
            'endpoints': {
                'POST /api/import/': {
                    'description': 'Importe des offres d\'emploi',
                    'methods': [
                        {
                            'name': 'File Upload',
                            'content_type': 'multipart/form-data',
                            'parameters': {
                                'csv_file': 'Fichier CSV (requis)',
                                'update': 'true/false - Met à jour les offres existantes',
                                'skip_invalid': 'true/false - Ignore les lignes invalides',
                                'delimiter': 'Délimiteur CSV (défaut: ,)'
                            }
                        },
                        {
                            'name': 'JSON Data',
                            'content_type': 'application/json',
                            'formats': [
                                {
                                    'jobs': [
                                        {
                                            'titre_poste': 'Développeur Python',
                                            'entreprise': 'TechCorp',
                                            'lieu': 'Dakar',
                                            'type_contrat': 'CDI',
                                            'url_offre': 'https://example.com/job/1'
                                        }
                                    ],
                                    'options': {
                                        'update': True,
                                        'skip_invalid': True
                                    }
                                }
                            ]
                        }
                    ]
                }
            },
            'required_fields': [
                'titre_poste', 'entreprise', 'lieu', 'type_contrat', 'url_offre'
            ],
            'optional_fields': [
                'date_publication', 'description', 'salaire', 'niveau_experience',
                'secteur_activite', 'competences', 'region', 'source_site'
            ]
        })