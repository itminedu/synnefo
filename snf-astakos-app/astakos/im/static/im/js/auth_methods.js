function placeTokenConfirm(){
	 
	var position = $('#token-span').parents('.form-row').position();
	$('#token-confirm').css('top', position.top - 10);
}

$(document).ready(function() {
	 	
	 	
	 $('.auth_methods .canremove').click( function(e) {
    	e.preventDefault(e);
    	$(this).parent('li').addClass('remove');
    	$(this).siblings('.dialog-wrap').slideDown('slow');
    });  
    
    $('.auth_methods .no').click( function(e) {
    	e.preventDefault(e);
    	$(this).parents('li').removeClass('remove');
    	$(this).parents('.dialog-wrap').slideUp('slow');
    });  
    
    $('.auth_methods .canremove').hover(
      function () {
      	$(this).siblings('span.details').hide();
      },
      function () {
      	$(this).siblings('span.details').show();
      });
	 	
	/* complex form js */
	
	// Intresting divs
	
	emailDiv = $('#id_email').parents('.form-row');
	newEmailDiv = $('#id_new_email_address').parents('.form-row');
	oldPasswordDiv = $('#id_old_password').parents('.form-row');
	newPassword1Div = $('#id_new_password1').parents('.form-row');
	newPassword2Div = $('#id_new_password2').parents('.form-row');
	emailCheck = $('#id_change_email').parents('.form-row');
	passwordCheck = $('#id_change_password').parents('.form-row');
	authTokenDiv = $('#id_auth_token').parents('.form-row');
	
	if ( newEmailDiv.length>0  ){ 
		emailDiv.addClass('form-following');
	}
	
	oldPasswordDiv.addClass('form-following');
	
	
	// Intresting img spans
	
	emailDiv.find('span.extra-img').attr('id','email-span');
	oldPasswordDiv.find('span.extra-img').attr('id','password-span');
	authTokenDiv.find('span.extra-img').attr('id','token-span');
	// Default hidden fields
	
	 
	emailCheck.hide();
	passwordCheck.hide();
	
	
	
	newEmailDiv.addClass('email-span');
	newPassword1Div.addClass('password-span');
	newPassword2Div.addClass('password-span');
	
	$('.password-span').wrapAll('<div class="hidden-form-rows">');
	$('.email-span').wrapAll('<div class="hidden-form-rows">');
	
	// If errors show fields
	
	
	if ($('input#id_change_password:checkbox').attr('checked')) {
		oldPasswordDiv.find('input').focus();
		$('.form-following #password-span').parents('.form-row').next('.hidden-form-rows').show();
		 
		$('.form-following #password-span').parents('.form-row').addClass('open');
	}; 
	
	
	
	if ($('input#id_change_email:checkbox').attr('checked')) {
		 
		$('.form-following #email-span').parents('.form-row').next('.hidden-form-rows').show();
		$('.form-following #email-span').parents('.form-row').addClass('open');
	}; 
	
	// Email, Password forms
	
	$('.form-following .extra-img').click(function(e){
		
		$(this).parents('.form-row').toggleClass('open');
		$(this).parents('.form-row').next('.hidden-form-rows').slideToggle('slow');
		
		id = $(this).attr('id');
		 
	 
	 	
	 	if ( !($(this).parents('.form-row').hasClass('open')) ){
	 		$('.form-row').each(function() {
				if( $(this).hasClass(id) ) {
					console.info($(this).find('input[type="text"]'));
					$(this).find('input').val('');
					$(this).removeClass('with-errors');
					$(this).find('.form-error').hide();
				}
			}); 
	 	} 	else {
	 		// focus on first input
	 		if ( id == 'email-span') { newEmailDiv.find('input').focus(); } 
	 		if ( id == 'password-span') { oldPasswordDiv.find('input').focus(); }
	 	}
	 	
	 	placeTokenConfirm();
	});
	
	//  check uncheck checkbox
	$('#email-span').click(function(){ 
       var $checkbox = $('input#id_change_email:checkbox');
       $checkbox.attr('checked', !$checkbox.attr('checked'));
 	});
	
	//  check uncheck checkbox
	$('#password-span').click(function(){ 
       var $checkbox = $('input#id_change_password:checkbox');
       $checkbox.attr('checked', !$checkbox.attr('checked'));
 	});
	
	// refresh token
	authTokenDiv.addClass('refresh');
	$('#token-span').click(function(e){
		$(this).parents('.form-row').toggleClass('open');
		$(this).siblings('span.info').find('span').hide();	
		placeTokenConfirm();
		$('#token-confirm').toggle();
		return false;
	});
	
	$('#token-confirm').click(function(e){
		e.preventDefault();
		renewToken();
		$(this).hide();
	})
	
	$('#token-span').hover(
      function () {
      	if (!$(this).parents('.form-row').hasClass('open')){
      		$(this).siblings('span.info').find('span').show();	
      	}
      	
      },
      function () {
      	$(this).siblings('span.info').find('span').hide();
      });
	
	/* end of complex form js */
	
	 
	    
});
	 