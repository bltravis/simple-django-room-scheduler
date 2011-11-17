import json
from datetime import datetime,timedelta,date

from django.template import RequestContext
from django.core.context_processors import csrf
from django.shortcuts import render_to_response
from django.conf import settings
from django.http import HttpResponseRedirect,HttpResponse
from django.core.exceptions import ValidationError
from django.db.models import Q

from roomkeys.models import RoomKey
from models import Reservation,Room,NPSUser


def default_view(request):
	dt_start = datetime.now()
	time_fix = timedelta(minutes=dt_start.minute % settings.RES_MIN_LENGTH, seconds=dt_start.second,
	  microseconds=dt_start.microsecond) # zero out seconds
	dt_start = dt_start - time_fix
	today = datetime.fromordinal(date.today().toordinal())
	
	floor = dt_start - timedelta(minutes=settings.RES_MIN_LENGTH)
	ceiling = dt_start + timedelta(hours=(settings.RES_LOOK_AHEAD_HOURS + 0.5))
	
	email = request.session.get('email', False)
	
	times = []
	rooms = []
	avail = []

	times.append('') # initial empty spot
	# generate time labels
	for i in range(0,(int(settings.RES_LOOK_AHEAD_HOURS + 0.5) * 60 / settings.RES_LOOK_AHEAD_INC)):
		t = dt_start + timedelta(minutes=i*settings.RES_LOOK_AHEAD_INC)
		times.append(':'.join(t.time().isoformat().split(':')[0:2]))
	
	# generate room reservation list
	for room in Room.objects.all():
		for time in times:
			if time == times[0]:
				rooms.append([])
				rooms[-1].append(room.name)
		
			else:
				(hour,minute) = time.split(':')
				adj = today + timedelta(hours=int(hour),minutes=int(minute))
				prev = adj - timedelta(minutes=settings.RES_LOOK_AHEAD_INC)
				next = adj + timedelta(minutes=settings.RES_LOOK_AHEAD_INC)
				cur = Reservation.objects.filter(
					Q(room=room),
					(Q(datetime_start__gte=prev) & Q(datetime_start__lte=next)) | Q(datetime_end__gte=prev),
				)

				if len(cur) == 0:
					rooms[-1].append(0)
				else:
					rooms[-1].append(cur[0].type)
	
	for i in range(0,len(times)):
		t = []
		t.append(times[i].replace(':',''))
		for room in rooms:
			t.append(room[i])
			
		avail.append(t)

	c = RequestContext(request, {
		'page_title': 'Welcome!',
		'avail': avail,
	})
	c.update(csrf(request))

	return render_to_response(settings.RES_DEFAULT_TEMPLATE, c)

def reserve(email,roombc,datetime_start,datetime_end):
	ret = {
		'success': False,
		'error': '',
	}
	
	try:
		user = NPSUser.objects.get(email=email)
	except:
		user = NPSUser()
		user.email = email
		user.date_last_booking = datetime.now() # mostly for daily quotas
		
	try:
		roomkey = RoomKey.objects.get(barcode=roombc)
		room = roomkey.room
	except:
		ret['error'] = 'That room doesn\'t appear to exist. Please select another room.'
		return ret

    # don't capture this if it errors because that means a core problem exists
    # that should be fixed.	
   	user.save()
	
	res = Reservation()
	res.requested_user = user
	res.room = room
	res.datetime_start = datetime_start
	res.datetime_end = datetime_end
	
	# Allow early returns of keys and semi-correction of reservation end datetimes.
	_ = return_key(roombc,False)
	
	try:
		res.clean() # not called automatically without a form
	except ValidationError as ve:
		# failure to clean, only validation errors are raised
		if len(ve.messages) > 1:
			for msg in ve.messages:
				ret['error'] += msg + '<br />'
		else:
			ret['error'] = ve.messages[0]
			
		return ret
	
	# again, if an exception occurs by now, it's likely a bug.		
	res.save()
	
	ret['success'] = True
	return ret
	
def ajax_reserve(request):
	ret = {
		'success': False,
		'error': '',
	}
	
	if request.method == 'GET':
		email = request.GET.get('email', '0')
		roombc = request.GET.get('barcode','0')
		minutes = int(request.GET.get('minutes','0'))
		
		# turns out it's okay to track actual datetime reserved.
		datetime_start = datetime.now()
		datetime_end = datetime_start + timedelta(minutes=minutes)
		
		ret = reserve(email, roombc, datetime_start, datetime_end)
		
	return HttpResponse(json.dumps(ret))
	
'''
Given a key's barcode, the reservation associated with the key is looked up and the
datetime_end is set to datetime.now().

If force is set to True, it will update a reservation's end datetime with now()
regardless of if a reservation ended earlier today.
'''
def return_key(barcode,force):
	ret = {
		'success': False,
		'error': '',
	}
	
	try:
		roomkey = RoomKey.objects.get(barcode=barcode)
	
	except:
		ret['error'] = 'That barcode doesn\'t appear to be in the system.'
		ret['success'] = False
		return ret
	
	try:
		room = Room.objects.get(pk=roomkey.pk)
		
	except:
		ret['error'] = 'The scanned key doesn\'t appear to be linked to a room.'
		ret['success'] = False
		return ret
		
	# find the latest entry for this room for today (prevent accidental return)
	today = datetime.now()
	# look only at the date
	today -= timedelta(hours=today.hour,minutes=today.minute,seconds=today.second,microseconds=today.microsecond)
	tomorrow = today + timedelta(hours=24)
	res = Reservation.objects.filter(
		room=room,
		datetime_start__gte=today,
		datetime_end__lte=tomorrow,
		type=1 # not closed
	).order_by('-datetime_start')
	
	if len(res) > 0:
		item = res[0] # get latest reservation
		
		if force == True or item.datetime_end >= datetime.now():
			# set the end datetime to now since we're checking the room key in now.
			# this not only aids in correcting early checkins, but late checkins as well.
			item.datetime_end = datetime.now()
			# don't bother cleaning since this reservation can't possibly conflict with
			# anything else since we're either returning early or returning in the process
			# of creating another reservation which won't check times until after this 
			# correction is saved...
			item.save()
	
	ret['success'] = True
	return ret
	
def ajax_return_key(request):
	ret = {
		'success': False,
		'error': '',
	}
	
	if request.method == 'GET':
		ret = return_key( request.GET.get('barcode', '0'), True )
		
	return HttpResponse(json.dumps(ret))
