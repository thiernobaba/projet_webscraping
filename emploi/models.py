# models.py
from django.db import models
from django.utils import timezone
from django.core.validators import URLValidator

class TruncatingCharField(models.CharField):
    """Un CharField qui tronque automatiquement les valeurs trop longues"""
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value and len(value) > self.max_length:
            value = value[:self.max_length]
        return value

class TruncatingURLField(models.URLField):
    """Un URLField qui tronque automatiquement les URLs trop longues"""
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value and len(value) > self.max_length:
            value = value[:self.max_length]
        return value

class Entreprise(models.Model):
    nom = TruncatingCharField(max_length=200, unique=True)
    tagline = TruncatingCharField(max_length=255, blank=True)
    secteur = TruncatingCharField(max_length=100, blank=True, null=True)
    taille = TruncatingCharField(max_length=50, blank=True, null=True)
    logo_url = TruncatingURLField(max_length=500, blank=True, null=True)
    site_web = TruncatingURLField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    description = models.TextField(blank=True, null=True)
    
    class Meta:
        verbose_name = "Entreprise"
        verbose_name_plural = "Entreprises"
    
    def __str__(self):
        return self.nom

class Region(models.Model):
    nom = TruncatingCharField(max_length=100, unique=True)
    code = TruncatingCharField(max_length=10, unique=True)
    
    def __str__(self):
        return self.nom

class OffreEmploi(models.Model):
    TYPE_CONTRAT_CHOICES = [
        ('CDI', 'Contrat à Durée Indéterminée'),
        ('CDD', 'Contrat à Durée Déterminée'),
        ('STAGE', 'Stage'),
        ('FREELANCE', 'Freelance'),
        ('TEMPS_PARTIEL', 'Temps Partiel'),
        ('PRESTATION DE SERVICES', 'Prestation de services'),
        ('CONSULTING', 'Consulting'),
        ('INDEFINI', 'Le type de contrat est indéfinie')
    ]
    
    NIVEAU_EXPERIENCE_CHOICES = [
        ('JUNIOR', '0-2 ans'),
        ('INTERMEDIAIRE', '3-5 ans'),
        ('SENIOR', '5-10 ans'),
        ('EXPERT', '10+ ans'),
    ]
    
    titre_poste = TruncatingCharField(max_length=200)
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name='offres')
    lieu = TruncatingCharField(max_length=200)
    region = models.ForeignKey(Region, on_delete=models.SET_NULL, null=True, blank=True)
    type_contrat = TruncatingCharField(max_length=30, choices=TYPE_CONTRAT_CHOICES)
    date_publication = models.DateTimeField()
    url_offre = TruncatingURLField(max_length=300)
    
    # Champs supplémentaires pour enrichir l'analyse
    description = models.TextField(blank=True, null=True)
    salaire_min = models.IntegerField(blank=True, null=True)
    salaire_max = models.IntegerField(blank=True, null=True)
    niveau_experience = TruncatingCharField(max_length=20, choices=NIVEAU_EXPERIENCE_CHOICES, blank=True, null=True)
    secteur_activite = TruncatingCharField(max_length=100, blank=True, null=True)
    competences = models.TextField(blank=True, null=True)
    
    # Métadonnées
    source_site = TruncatingCharField(max_length=100, default='scraped')
    date_scraping = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    vues = models.IntegerField(default=0)
    
    class Meta:
        verbose_name = "Offre d'emploi"
        verbose_name_plural = "Offres d'emploi"
        ordering = ['-date_publication']
        indexes = [
            models.Index(fields=['date_publication']),
            models.Index(fields=['region']),
            models.Index(fields=['type_contrat']),
            models.Index(fields=['secteur_activite']),
        ]
    
    def __str__(self):
        return f"{self.titre_poste} - {self.entreprise.nom}"
    
    def save(self, *args, **kwargs):
        # Normalisation du type de contrat
        if self.type_contrat:
            self.type_contrat = self.type_contrat.upper().strip()
            if 'PRESTATION' in self.type_contrat:
                self.type_contrat = 'PRESTATION DE SERVICES'
            elif 'CONSULT' in self.type_contrat:
                self.type_contrat = 'CONSULTING'
            elif 'TEMPS PARTIEL' in self.type_contrat:
                self.type_contrat = 'TEMPS_PARTIEL'
        
        super().save(*args, **kwargs)
    
    @property
    def is_recent(self):
        return (timezone.now() - self.date_publication).days <= 7
    
    @property
    def salaire_range(self):
        if self.salaire_min and self.salaire_max:
            return f"{self.salaire_min:,} - {self.salaire_max:,} FCFA"
        elif self.salaire_min:
            return f"À partir de {self.salaire_min:,} FCFA"
        return "Non spécifié"

class StatistiquesGlobales(models.Model):
    """Modèle pour stocker des statistiques pré-calculées"""
    date_calcul = models.DateTimeField(auto_now=True)
    total_offres = models.IntegerField(default=0)
    total_entreprises = models.IntegerField(default=0)
    offres_ce_mois = models.IntegerField(default=0)
    secteur_le_plus_actif = TruncatingCharField(max_length=100, blank=True)
    region_la_plus_active = TruncatingCharField(max_length=100, blank=True)
    salaire_moyen = models.FloatField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Statistiques globales"
        verbose_name_plural = "Statistiques globales"