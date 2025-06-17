# emploi/templatetags/custom_filters.py
from django import template

register = template.Library()

@register.filter(name='mul')
def mul(value, arg):
    """Multiplie la valeur par l'argument"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0
    
@register.filter(name='split')
def split(value, arg):
    return value.split(arg)