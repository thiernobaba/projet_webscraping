# views.py - Version corrigée

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
import requests
import pytz

from emploi.models import OffreEmploi, Entreprise, Region

logger = logging.getLogger(__name__)

@method_decorator(csrf_exempt, name='dispatch')
class ImportJobsAPIView(View):
    """API pour importer des offres d'emploi"""
    
    def dispatch(self, request, *args, **kwargs):
        """Ajouter les headers CORS"""
        if request.method == 'OPTIONS':
            return self.options(request, *args, **kwargs)
            
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
            content_type = request.content_type or ''
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
            logger.error(f"Erreur dans l'API d'import: {str(e)}", exc_info=True)
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
        
        # Vérifier la taille du fichier (limite à 10MB)
        if csv_file.size > 10 * 1024 * 1024:
            return JsonResponse({
                'success': False,
                'error': 'Fichier trop volumineux (limite: 10MB)'
            }, status=400)
        
        options = self.get_options_from_request(request)
        
        try:
            # Essayer UTF-8 d'abord, puis ISO-8859-1 si échec
            try:
                content = csv_file.read().decode('utf-8')
            except UnicodeDecodeError:
                csv_file.seek(0)
                content = csv_file.read().decode('iso-8859-1')
            
            return self.process_csv_content(content, options)
            
        except Exception as e:
            logger.error(f"Erreur lecture fichier: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Erreur lecture fichier: {str(e)}'
            }, status=400)

    def handle_json_data(self, request):
        """Gère les données JSON directes"""
        try:
            data = json.loads(request.body.decode('utf-8'))
            logger.info(f"Données JSON reçues")
        except json.JSONDecodeError as e:
            logger.error(f"JSON invalide: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': 'JSON invalide'
            }, status=400)
        except UnicodeDecodeError as e:
            logger.error(f"Erreur d'encodage: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': 'Erreur d\'encodage des données'
            }, status=400)
        
        # Vérification des données requises
        if 'jobs' in data:
            if not isinstance(data['jobs'], list):
                return JsonResponse({
                    'success': False,
                    'error': 'Le champ "jobs" doit être une liste'
                }, status=400)
                
            return self.handle_job_list(data['jobs'], data.get('options', {}))
        elif 'csv_content' in data:
            return self.process_csv_content(data['csv_content'], data.get('options', {}))
        elif 'csv_url' in data:
            return self.handle_csv_url(data['csv_url'], data.get('options', {}))
        else:
            return JsonResponse({
                'success': False,
                'error': 'Format JSON invalide. Utilisez "jobs", "csv_content" ou "csv_url"'
            }, status=400)

    def handle_csv_url(self, csv_url, options):
        """Télécharge et traite un fichier CSV depuis une URL"""
        try:
            # Validation basique de l'URL
            if not csv_url.startswith(('http://', 'https://')):
                return JsonResponse({
                    'success': False,
                    'error': 'URL invalide'
                }, status=400)
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(csv_url, timeout=30, headers=headers)
            response.raise_for_status()
            
            # Détection de l'encodage
            encoding = response.encoding or 'utf-8'
            content = response.content.decode(encoding)
            
            return self.process_csv_content(content, options)
        except requests.RequestException as e:
            logger.error(f"Erreur téléchargement URL: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Erreur lors du téléchargement: {str(e)}'
            }, status=400)
        except UnicodeDecodeError as e:
            logger.error(f"Erreur d'encodage URL: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': 'Erreur d\'encodage du fichier distant'
            }, status=400)

    def handle_job_list(self, jobs, options):
        """Traite une liste d'offres d'emploi en JSON"""
        if not jobs:
            return JsonResponse({
                'success': False,
                'error': 'Aucune offre fournie'
            }, status=400)
            
        stats = {
            'total': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'missing_fields': 0
        }
        
        errors = []
        
        for idx, job_data in enumerate(jobs):
            stats['total'] += 1
            job_id = f"#{idx+1}"
            
            try:
                with transaction.atomic():
                    result = self.process_single_job(job_data, options)
                    stats[result['action']] += 1
                    stats['missing_fields'] += result.get('missing_count', 0)
                    
            except Exception as e:
                stats['errors'] += 1
                error_msg = str(e)
                errors.append({
                    'job': job_id,
                    'error': error_msg,
                    'job_data': {k: v for k, v in job_data.items() if k in ['titre_poste', 'entreprise', 'lieu']}
                })
                logger.error(f"Erreur offre {job_id}: {error_msg}")
                
                if not options.get('skip_invalid', False):
                    break
        
        return JsonResponse({
            'success': True,
            'stats': stats,
            'errors': errors[:10]  # Limiter les erreurs affichées
        })

    def process_csv_content(self, content, options):
        """Traite le contenu CSV"""
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
                try:
                    sniffer = csv.Sniffer()
                    delimiter = sniffer.sniff(content[:1024]).delimiter
                except csv.Error:
                    delimiter = ','  # Fallback
            
            # Nettoyage du contenu
            content = content.strip()
            if not content:
                return JsonResponse({
                    'success': False,
                    'error': 'Fichier CSV vide'
                }, status=400)
            
            reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
            
            # Vérifier si le fichier a des en-têtes
            if not reader.fieldnames:
                return JsonResponse({
                    'success': False,
                    'error': 'Aucun en-tête trouvé dans le fichier CSV'
                }, status=400)
            
            for idx, row in enumerate(reader):
                stats['total'] += 1
                line_num = idx + 2  # +1 pour l'en-tête, +1 pour 1-based
                
                # Ignorer les lignes vides
                if not any(row.values()):
                    stats['skipped'] += 1
                    continue
                
                try:
                    with transaction.atomic():
                        result = self.process_single_job(dict(row), options)
                        stats[result['action']] += 1
                        stats['missing_fields'] += result.get('missing_count', 0)
                        
                except Exception as e:
                    stats['errors'] += 1
                    error_msg = str(e)
                    errors.append({
                        'line': line_num,
                        'error': error_msg,
                        'data': {k: v for k, v in row.items() if k in ['titre_poste', 'entreprise', 'lieu']}
                    })
                    logger.error(f"Erreur ligne {line_num}: {error_msg}")
                    
                    if not options.get('skip_invalid', False):
                        break
            
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
        """Traite une seule offre d'emploi"""
        offre_data, missing_count = self.prepare_offre_data(job_data)
        
        # Validation des champs obligatoires
        required_fields = ['titre_poste', 'entreprise', 'lieu', 'type_contrat', 'url_offre']
        missing_required = []
        
        for field in required_fields:
            if not offre_data.get(field) or str(offre_data.get(field)).strip() == '':
                missing_required.append(field)
        
        if missing_required:
            raise ValidationError(f"Champs obligatoires manquants: {', '.join(missing_required)}")
        
        # Créer ou récupérer l'entreprise
        entreprise, _ = Entreprise.objects.get_or_create(
            nom=offre_data['entreprise'][:200],  # Limiter la taille
            defaults={
                'tagline': offre_data.get('tagline', '')[:500],
                'site_web': offre_data.get('site_web', '')[:200],
                'secteur': offre_data.get('secteur', '')[:100]
            }
        )

        # Créer ou récupérer la région
        region = None
        if offre_data.get('region') and offre_data['region'] not in ['NA', '']:
            region, _ = Region.objects.get_or_create(
                nom=offre_data['region'][:100],
                defaults={'code': offre_data.get('region_code', '')[:10]}
            )

        # Vérifier si une offre identique existe déjà
        create_fields = self.get_create_fields(offre_data, entreprise, region)
        
        # Requête pour trouver des doublons basée sur URL unique
        duplicate_query = OffreEmploi.objects.filter(
            url_offre=create_fields['url_offre']
        )
        
        if duplicate_query.exists():
            if options.get('update', False):
                # Mise à jour de l'offre existante
                offre = duplicate_query.first()
                for field, value in create_fields.items():
                    if field != 'url_offre':  # Ne pas modifier l'URL
                        setattr(offre, field, value)
                offre.full_clean()
                offre.save()
                return {'action': 'updated', 'missing_count': missing_count}
            else:
                return {'action': 'skipped', 'missing_count': missing_count, 'reason': 'duplicate'}
        
        # Créer une nouvelle offre
        offre = OffreEmploi(**create_fields)
        offre.full_clean()
        offre.save()
        
        return {'action': 'created', 'missing_count': missing_count}

    def prepare_offre_data(self, row):
        """Prépare les données d'une offre d'emploi"""
        missing_count = 0
        
        def get_field(field, default=''):
            nonlocal missing_count
            value = row.get(field, '')
            if value is None:
                value = ''
            value = str(value).strip()
            
            if not value or value.lower() in ['null', 'none', 'na', '']:
                if default == 'NA':
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
                # Nettoyer les caractères non numériques sauf le tiret
                salaire_clean = ''.join(c for c in salaire if c.isdigit() or c in ['-', ' '])
                
                if '-' in salaire_clean:
                    salaire_parts = salaire_clean.split('-')
                    if len(salaire_parts) == 2:
                        salaire_min = int(salaire_parts[0].strip())
                        salaire_max = int(salaire_parts[1].strip())
                else:
                    salaire_min = int(salaire_clean.strip())
            except (ValueError, TypeError):
                pass

        # Normalisation du type de contrat
        type_contrat = get_field('type_contrat', 'INDEFINI').upper()
        valid_contracts = ['CDI', 'CDD', 'STAGE', 'FREELANCE', 'INTERIM', 'INDEFINI']
        if type_contrat not in valid_contracts:
            type_contrat = 'INDEFINI'

        # Normalisation du niveau d'expérience
        niveau_exp = get_field('niveau_experience', '').upper()
        valid_levels = ['JUNIOR', 'SENIOR', 'EXPERT', 'DEBUTANT', 'CONFIRME']
        if niveau_exp not in valid_levels:
            niveau_exp = ''

        prepared_data = {
            'titre_poste': get_field('titre_poste')[:200],
            'entreprise': get_field('entreprise')[:200],
            'tagline': get_field('tagline', '')[:500],
            'site_web': get_field('site_web', '')[:200],
            'secteur': get_field('secteur', '')[:100],
            'lieu': get_field('lieu')[:100],
            'region': get_field('region', '')[:100],
            'region_code': get_field('region_code', '')[:10],
            'type_contrat': type_contrat,
            'date_publication': date_publication or timezone.now(),
            'url_offre': get_field('url_offre')[:500],
            'description': get_field('description', '')[:2000],
            'salaire_min': salaire_min,
            'salaire_max': salaire_max,
            'niveau_experience': niveau_exp,
            'secteur_activite': get_field('secteur_activite', '')[:100],
            'competences': get_field('competences', '')[:1000],
            'source_site': get_field('source_site', 'api')[:50]
        }
        
        return prepared_data, missing_count

    def get_create_fields(self, offre_data, entreprise, region):
        """Champs pour la création"""
        return {
            'titre_poste': offre_data['titre_poste'],
            'entreprise': entreprise,
            'lieu': offre_data['lieu'],
            'region': region,
            'type_contrat': offre_data['type_contrat'],
            'date_publication': offre_data['date_publication'],
            'url_offre': offre_data['url_offre'],
            'description': offre_data['description'],
            'salaire_min': offre_data.get('salaire_min'),
            'salaire_max': offre_data.get('salaire_max'),
            'niveau_experience': offre_data.get('niveau_experience'),
            'secteur_activite': offre_data.get('secteur_activite'),
            'competences': offre_data.get('competences'),
            'source_site': offre_data.get('source_site')
        }

    def parse_date(self, date_str):
        """Parse différents formats de date"""
        if not date_str or date_str == 'NA':
            return None
            
        # Nettoyer la chaîne de date
        date_str = str(date_str).strip()
        
        # Gérer les formats français avec texte
        if 'publié le' in date_str.lower():
            # Extraire la date après "Publié le "
            date_str = date_str.lower().replace('publié le ', '').strip()
        
        # Dictionnaire pour les mois français
        mois_francais = {
            'janvier': '01', 'février': '02', 'mars': '03', 'avril': '04',
            'mai': '05', 'juin': '06', 'juillet': '07', 'août': '08',
            'septembre': '09', 'octobre': '10', 'novembre': '11', 'décembre': '12'
        }
        
        # Traiter les formats français comme "18 juin 2025"
        for mois_fr, mois_num in mois_francais.items():
            if mois_fr in date_str.lower():
                try:
                    # Remplacer le mois français par le numéro
                    parts = date_str.lower().split()
                    if len(parts) >= 3:
                        jour = parts[0]
                        annee = parts[2]
                        # Construire la date au format ISO
                        date_iso = f"{annee}-{mois_num}-{jour.zfill(2)}"
                        parsed_date = datetime.strptime(date_iso, '%Y-%m-%d')
                        now = datetime.now()
                        if parsed_date.year <= now.year + 10:
                            return timezone.make_aware(parsed_date, timezone.get_current_timezone())
                except (ValueError, IndexError):
                    continue
                break
        
        # Formats standards
        formats = [
            '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', 
            '%d-%m-%Y', '%Y/%m/%d', '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%SZ',
            '%d %B %Y', '%B %d, %Y'
        ]
        
        for fmt in formats:
            try:
                parsed_date = datetime.strptime(date_str, fmt)
                # S'assurer que la date est dans le futur proche (pas plus de 10 ans dans le futur)
                now = datetime.now()
                if parsed_date.year > now.year + 10:
                    continue
                return timezone.make_aware(parsed_date, timezone.get_current_timezone())
            except (ValueError, TypeError):
                continue
        
        logger.warning(f"Format de date non reconnu: {date_str}")
        return None

    def get_options_from_request(self, request):
        """Extrait les options de la requête"""
        def to_bool(value):
            if isinstance(value, bool):
                return value
            return str(value).lower() in ['true', '1', 'on', 'yes']
        
        options = {
            'update': to_bool(request.POST.get('update', False)),
            'skip_invalid': to_bool(request.POST.get('skip_invalid', True)),
            'delimiter': request.POST.get('delimiter', ',')
        }
        
        return options

    def get(self, request):
        """Documentation de l'API"""
        return JsonResponse({
            'api_name': 'Job Import API',
            'version': '1.2',
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
                                'update': 'true/false - Met à jour les offres existantes (défaut: false)',
                                'skip_invalid': 'true/false - Ignore les lignes invalides (défaut: true)',
                                'delimiter': 'Délimiteur CSV (défaut: ,)'
                            }
                        },
                        {
                            'name': 'JSON Data',
                            'content_type': 'application/json',
                            'example': {
                                'jobs': [
                                    {
                                        'titre_poste': 'Développeur Python',
                                        'entreprise': 'TechCorp',
                                        'lieu': 'Dakar',
                                        'type_contrat': 'CDI',
                                        'url_offre': 'https://example.com/job/1',
                                        'description': 'Description du poste...',
                                        'salaire': '500000-800000',
                                        'date_publication': '2025-06-20'
                                    }
                                ],
                                'options': {
                                    'update': True,
                                    'skip_invalid': True
                                }
                            }
                        },
                        {
                            'name': 'CSV URL',
                            'content_type': 'application/json',
                            'example': {
                                'csv_url': 'https://example.com/jobs.csv',
                                'options': {
                                    'delimiter': ',',
                                    'update': False
                                }
                            }
                        }
                    ]
                }
            },
            'required_fields': [
                'titre_poste', 'entreprise', 'lieu', 'type_contrat', 'url_offre'
            ],
            'optional_fields': [
                'description', 'salaire', 'date_publication', 'niveau_experience',
                'secteur_activite', 'competences', 'region', 'tagline', 'site_web'
            ],
            'valid_contract_types': ['CDI', 'CDD', 'STAGE', 'FREELANCE', 'INTERIM'],
            'valid_experience_levels': ['JUNIOR', 'SENIOR', 'EXPERT', 'DEBUTANT', 'CONFIRME']
        })
