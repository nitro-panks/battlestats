from django.contrib import admin
from django.urls import path, include
from rest_framework import routers
from warships.views import PlayerViewSet, ClanViewSet, ShipViewSet
from warships.views import tier_data, activity_data, type_data, randoms_data, ranked_data, clan_members, clan_data, clan_battle_seasons, landing_clans, landing_players, landing_recent_players, player_name_suggestions, player_summary, players_explorer, wr_distribution, db_stats
from django.conf import settings
from django.conf.urls.static import static

router = routers.DefaultRouter()
router.register(r'player', PlayerViewSet, basename='player')
router.register(r'clan', ClanViewSet, basename='clan')
router.register(r'ship', ShipViewSet, basename='ship')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),  # Include the router URLs
    path('api-auth/', include('rest_framework.urls', namespace='rest_framework')),
    path('api/fetch/tier_data/<str:player_id>/',
         tier_data, name='fetch_tier_data'),
    path('api/fetch/activity_data/<str:player_id>/',
         activity_data, name='fetch_activity_data'),
    path('api/fetch/type_data/<str:player_id>/',
         type_data, name='fetch_type_data'),
    path('api/fetch/randoms_data/<str:player_id>/',
         randoms_data, name='fetch_randoms_data'),
    path('api/fetch/ranked_data/<str:player_id>/',
         ranked_data, name='fetch_ranked_data'),
    path('api/fetch/player_summary/<str:player_id>/',
         player_summary, name='fetch_player_summary'),
    path('api/fetch/clan_members/<str:clan_id>/',
         clan_members, name='fetch_clan_members'),
    path('api/fetch/clan_data/<str:clan_filter>',
         clan_data, name='fetch_clan_data'),
    path('api/fetch/clan_battle_seasons/<str:clan_id>/',
         clan_battle_seasons, name='fetch_clan_battle_seasons'),
    path('api/landing/clans/',
         landing_clans, name='landing_clans'),
    path('api/landing/players/',
         landing_players, name='landing_players'),
    path('api/landing/player-suggestions/',
         player_name_suggestions, name='player_name_suggestions'),
    path('api/landing/recent/',
         landing_recent_players, name='landing_recent_players'),
    path('api/players/explorer/',
         players_explorer, name='players_explorer'),
    path('api/fetch/wr_distribution/',
         wr_distribution, name='fetch_wr_distribution'),
    path('api/stats/',
         db_stats, name='db_stats'),

]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL,
                          document_root=settings.STATIC_ROOT)
