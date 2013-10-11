# -*- coding: utf-8 -*-

#
# Freesound is (c) MUSIC TECHNOLOGY GROUP, UNIVERSITAT POMPEU FABRA
#
# Freesound is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Freesound is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#     See AUTHORS file.
#

from sounds.models import Sound
from django.contrib.auth.models import User
from apiv2.serializers import SoundSerializer, SoundListSerializer, UserSerializer, UploadAudioFileSerializer
from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view, authentication_classes
from apiv2.authentication import OAuth2Authentication, TokenAuthentication, SessionAuthentication
from forms import ApiV2ClientForm
from models import ApiV2Client
from django.contrib.auth.decorators import login_required
from django.shortcuts import render_to_response
from django.template import RequestContext
from utils import get_authentication_details_form_request, ListAPIView, RetrieveAPIView, WriteRequiredGenericAPIView
from exceptions import NotFoundException, InvalidUrlException, UnauthorizedException, ServerErrorException
import settings
import logging
from django.http import HttpResponseRedirect
from django.core.urlresolvers import reverse
import datetime
from provider.oauth2.models import AccessToken, Grant
from api.models import ApiKey
from django.contrib import messages
from accounts.views import handle_uploaded_file

logger = logging.getLogger("api")


@api_view(('GET',))
def api_root(request, format=None):
    return Response({
        #'sounds': reverse('apiv2-sound-list', request=request, format=format),
    })


class SoundDetail(RetrieveAPIView):
    """
    Detailed sound information.
    """
    authentication_classes = (OAuth2Authentication, TokenAuthentication, SessionAuthentication)
    serializer_class = SoundSerializer
    queryset = Sound.objects.filter(moderation_state="OK", processing_state="OK")


class UserDetail(RetrieveAPIView):
    """
    Detailed user information.
    """
    authentication_classes = (OAuth2Authentication, TokenAuthentication, SessionAuthentication)
    serializer_class = UserSerializer
    queryset = User.objects.filter(is_active=True)


class UserSoundList(ListAPIView):
    """
    List of sounds uploaded by user.
    """
    authentication_classes = (OAuth2Authentication, TokenAuthentication, SessionAuthentication)
    serializer_class = SoundListSerializer

    def get(self, request,  *args, **kwargs):
        print "Auth method: %s, Developer: %s, End user: %s" % get_authentication_details_form_request(request)
        logger.info("Test log")
        return super(UserSoundList, self).get(request, *args, **kwargs)

    def get_queryset(self):
        try:
            User.objects.get(id=self.kwargs['pk'], is_active=True)
        except User.DoesNotExist:
            raise NotFoundException()

        return Sound.objects.select_related('user').filter(moderation_state="OK",
                                                           processing_state="OK",
                                                           user__id=self.kwargs['pk'])


class UploadAudioFile(WriteRequiredGenericAPIView):
    """
    Upload a sound (without description)
    """
    authentication_classes = (OAuth2Authentication, SessionAuthentication)
    serializer_class = UploadAudioFileSerializer

    def post(self, request,  *args, **kwargs):

        # Get request information
        auth_method_name, developer, user = get_authentication_details_form_request(request)

        # Check if client has write permissions
        if auth_method_name == "OAuth2":
            if "write" not in request.auth.get_scope_display():
                raise UnauthorizedException

        serializer = UploadAudioFileSerializer(data=request.DATA, files=request.FILES)
        if serializer.is_valid():
            audiofile = request.FILES['audiofile']
            try:
                handle_uploaded_file(user.id, audiofile)
            except:
                raise ServerErrorException

            return Response(data={'details': 'File successfully uploaded (%s, %i)' % (audiofile.name, audiofile.size)}, status=status.HTTP_201_CREATED)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



############
# OTHER VEWS
############


### View for returning "Invalid url" 400 responses
@api_view(['GET'])
@authentication_classes([OAuth2Authentication, TokenAuthentication, SessionAuthentication])
def return_invalid_url(request):
    raise InvalidUrlException


### View for applying for an apikey
@login_required
def create_apiv2_key(request):

    if request.method == 'POST':
        form = ApiV2ClientForm(request.POST)
        if form.is_valid():
            api_client = ApiV2Client()
            api_client.user = request.user
            api_client.description = form.cleaned_data['description']
            api_client.name = form.cleaned_data['name']
            api_client.url = form.cleaned_data['url']
            api_client.redirect_uri = form.cleaned_data['redirect_uri']
            api_client.accepted_tos = form.cleaned_data['accepted_tos']
            api_client.save()
            form = ApiV2ClientForm()
    else:
        form = ApiV2ClientForm()

    if settings.APIV2KEYS_ALLOWED_FOR_APIV1:
        user_credentials = list(request.user.apiv2_client.all()) + list(request.user.api_keys.all())
    else:
        user_credentials = request.user.apiv2_client.all()
    fs_callback_url = request.build_absolute_uri(reverse('permission-granted'))

    return render_to_response('api/apply_key_apiv2.html',
                              {'user': request.user,
                               'form': form,
                               'user_credentials': user_credentials,
                               'combined_apiv1_and_apiv2': settings.APIV2KEYS_ALLOWED_FOR_APIV1,
                               'fs_callback_url': fs_callback_url,
                               }, context_instance=RequestContext(request))

### View for deleting api clients (works both for apiv2 and apiv1)
@login_required
def delete_api_credential(request, key):
    name = ""

    try:
        client = ApiV2Client.objects.get(key=key)
        name = client.name
        client.delete()
    except ApiV2Client.DoesNotExist:
        pass

    try:
        client = ApiKey.objects.get(key=key)
        name = client.name
        client.delete()
    except ApiKey.DoesNotExist:
        pass

    messages.add_message(request, messages.INFO, "Credentials with name %s have been deleted." % name)
    return HttpResponseRedirect(reverse("apiv2-apply"))


### View for managing permissions granted to apps
@login_required
def granted_permissions(request):
    user = request.user
    tokens_raw = AccessToken.objects.select_related('client').filter(user=user).order_by('-expires')
    tokens = []
    token_names = []

    # If settings.OAUTH_SINGLE_ACCESS_TOKEN is set to false it is possible that one single user have more than one active
    # access token per application. In that case we only show the one that expires later (and all are removed if permissions
    # revoked). If settings.OAUTH_SINGLE_ACCESS_TOKEN is set to true we don't need the token name check below because
    # there can only be one access token per client-user pair. Nevertheless the code below works in both cases.

    for token in tokens_raw:
        if not token.client.apiv2_client.name in token_names:
            tokens.append({
                'client_name': token.client.apiv2_client.name,
                'expiration_date': token.expires,
                'expired': (token.expires - datetime.datetime.today()).total_seconds() < 0,
                'scope': token.client.apiv2_client.get_scope_display,
                'client_id': token.client.apiv2_client.client_id,
            })
        token_names.append(token.client.apiv2_client.name)

    return render_to_response('api/manage_permissions.html',
                              {'user': request.user, 'tokens': tokens},
                              context_instance=RequestContext(request))


### View to revoke permissions granted to an application
@login_required
def revoke_permission(request, client_id):
    user = request.user
    tokens = AccessToken.objects.filter(user=user, client__client_id=client_id)
    for token in tokens:
        token.delete()

    return HttpResponseRedirect(reverse("access-tokens"))


### View to show grant code (pin code) if application does not support redirection
@login_required
def permission_granted(request):
    user = request.user
    code = request.GET.get('code', None)
    app_name = None
    try:
        grant = Grant.objects.get(user=user, code=code)
        app_name = grant.client.apiv2_client.name
    except:
        grant = None

    if settings.USE_MINIMAL_TEMPLATES_FOR_OAUTH:
        template = 'api/minimal_app_authorized.html'
    else:
        template = 'api/app_authorized.html'

    return render_to_response(template,
                              {'code': code, 'app_name': app_name},
                              context_instance=RequestContext(request))


