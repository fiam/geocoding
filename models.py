# -*- coding: utf-8 -*-

# Copyright (c) 2008 Alberto García Hierro <fiam@rm-fr.net>

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from django.db import models
from django.conf import settings
from django.utils import simplejson
from django.utils.translation import ugettext as _

from geonames.models import Geoname, Country

from datetime import datetime
from xml.etree.cElementTree import iterparse
from urllib2 import urlopen, quote
from decimal import Decimal, InvalidOperation

GOOGLE_GEOCODE_URI = 'http://maps.google.com/maps/geo?key=%(key)s&' \
        'oe=utf8&output=xml&q=%(q)s'
GOOGLE_REVERSE_GEOCODE_URI = 'http://maps.google.com/maps/nav?key=%(key)s&' \
        'oe=utf8&q=from%%3A%%20%(latitude)s%%2C%(longitude)s%%20to%%3A%%20' \
        '%(latitude)s%%2C%(longitude)s'

class BigIntegerField(models.IntegerField):
    empty_strings_allowed = False

    def get_internal_type(self):
        return 'BigIntegerField'

    def db_type(self):
        if settings.DATABASE_ENGINE == 'oracle':
            return 'NUMBER(19)'

        return 'BIGINT'

class GeocodedPoint(models.Model):
    hash = BigIntegerField(primary_key=True)
    status = models.IntegerField(null=True)
    accuracy = models.IntegerField(null=True)
    latitude = models.DecimalField(max_digits=20, decimal_places=17, null=True)
    longitude = models.DecimalField(max_digits=20, decimal_places=17, null=True)
    altitude = models.DecimalField(max_digits=10, decimal_places=5, null=True)
    address = models.CharField(max_length=300, null=True)
    thoroughfare_name = models.CharField(max_length=200, null=True)
    locality_name = models.CharField(max_length=200, null=True)
    dependent_locality_name = models.CharField(max_length=200, null=True)
    country = models.ForeignKey(Country, null=True)
    near = models.ForeignKey(Geoname, null=True, related_name='near_points')
    location = models.ForeignKey(Geoname, null=True,
        related_name='located_points')
    created = models.DateTimeField(default=datetime.now)

    def __unicode__(self):
        return (self.thoroughfare_name or 'near ' + unicode(self.near)) \
                + ' (%s, %s)' % (self.latitude, self.longitude)

    def success(self):
        return int(self.status) == 200

    def match(self):
        if self.latitude and self.longitude:
            for max_distance in (1, 3, 5, 10, 50, 100):
                nears = Geoname.near_point(self.latitude, self.longitude,
                    kms=max_distance)
                if not nears:
                    continue
                self.near = nears[0][0]
                self.country_id = nears[0][0].country_id
                if self.dependent_locality_name:
                    for near in nears:
                        if near[0].name == self.dependent_locality_name:
                            self.location = near[0]
                            self.save()
                            return self
                if self.locality_name:
                    for near in nears:
                        if near[0].name == self.locality_name:
                            self.location = near[0]
                            self.save()
                            return self
                for near in nears:
                    if near[0].population:
                        self.location = near[0]
                        self.save()
                        return self
        self.save()
        return self

    @property
    def near_name(self):
        try:
            return self.near.i18n_name
        except AttributeError:
            return u''
    
    @property
    def location_name(self):
        try:
            return self.location.i18n_name
        except AttributeError:
            return u''

    @property
    def parent_name(self):
        try:
            return self.near.parent.i18n_name
        except AttributeError:
            return u''

    @property
    def country_name(self):
        try:
            return self.country.geoname.i18n_name
        except AttributeError:
            return u''

    @property
    def request_id(self):
        return str(self.hash)

    @property
    def display_name(self):
        if self.near:
            return _('%(name)s near %(near_name)s in %(location_name)s,' \
                    ' %(country_name)s') % \
                {
                    'name': self.thoroughfare_name or _('Somewhere'),
                    'near_name': self.near_name,
                    'location_name': self.location_name or self.parent_name,
                    'country_name': self.country_name,
                }

        return _('Somewhere')

    @property
    def tz_dst(self):
        try:
            return self.near.timezone.dst_offset
        except AttributeError:
            return None

def direct_geocode(address):
    h = hash(address)
    point, created = GeocodedPoint.objects.get_or_create(hash=h)
    if not created:
        return point

    fp = urlopen(GOOGLE_GEOCODE_URI % { 'key': settings.GOOGLE_JS_API_KEY,
            'q': quote(address.encode('utf8')) })
    for event, element in iterparse(fp):
        if element.tag == '{http://earth.google.com/kml/2.0}code':
            point.status = int(element.text)
            if point.status != 200:
                break
        elif element.tag == '{http://earth.google.com/kml/2.0}address':
            point.address = element.text
        elif element.tag == '{urn:oasis:names:tc:ciq:xsdschema:' \
                'xAL:2.0}CountryNameCode':
            point.country_id = element.text 
        elif element.tag == '{urn:oasis:names:tc:ciq:xsdschema:' \
                'xAL:2.0}LocalityName':
            point.locality_name = element.text
        elif element.tag == '{urn:oasis:names:tc:ciq:xsdschema:' \
                'xAL:2.0}DependentLocalityName':
            point.dependent_locality_name = element.text
        elif element.tag == '{urn:oasis:names:tc:ciq:xsdschema:' \
                'xAL:2.0}ThoroughfareName':
            point.thoroughfare_name = element.text
        elif element.tag == '{urn:oasis:names:tc:ciq:xsdschema:' \
                'xAL:2.0}AddressDetails':
            point.accuracy = int(element.attrib['Accuracy'])
        elif element.tag == '{http://earth.google.com/kml/2.0}coordinates':
            point.longitude, point.latitude, point.altitude = \
                [Decimal(x) for x in element.text.split(',')]

    return point.match()

def reverse_geocode(latitude, longitude):
    if not (-85 < latitude < 85) or not (-180 < longitude < 180):
        return GeocodedPoint(status=400)
    h = hash('%s,%s' % (latitude, longitude))
    point, created = GeocodedPoint.objects.get_or_create(hash=h)
    if not created:
        return point

    fp = urlopen(GOOGLE_REVERSE_GEOCODE_URI % { 'key': settings.GOOGLE_JS_API_KEY, 'latitude': latitude, 'longitude': longitude })
    data = simplejson.load(fp)
    fp.close()
    point.status = data['Status']['code']
    if point.status != 200:
        if point.status == 604:
            point.status = 200
        point.latitude, point.longitude = [Decimal(str(x)) for x in (latitude, longitude)]
        return point.match()
    
    point.address = data['Placemark'][0]['address']
    point.accuracy = data['Placemark'][0]['AddressDetails']['Accuracy']
    point.thoroughfare_name = data['Placemark'][0]['AddressDetails']['Thoroughfare']['ThoroughfareName']
    if point.thoroughfare_name == 'Unknown road':
        point.thoroughfare_name = None
    point.latitude, point.longitude = [Decimal(str(x)) for x in (latitude, longitude)]
    point.altitude = Decimal(str(data['Placemark'][0]['Point']['coordinates'][2]))
    #point.longitude, point.latitude, point.altitude = [Decimal(str(x)) for x in data['Placemark'][0]['Point']['coordinates']]

    return point.match()

def geocode(query):
    try:
        latitude, longitude = [Decimal(x) for x in query.split(',')]
        return reverse_geocode(latitude, longitude)
    except (ValueError, TypeError, InvalidOperation):
        return direct_geocode(query)

