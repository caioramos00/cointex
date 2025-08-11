/*
 * show pass
 * otp input
 * back page
 * clear text
 * active suggestions
 * change value
 * modal bootstrap click handler
 * tab slide 
 * show notification
 * hide popup 
 * preloader
 */
(function ($) {
  "use strict";

  /* show pass
  ------------------------------------------------------------------------------------- */
  var showPass = function () {
    $(".show-pass").on("click", function () {
      $(this).toggleClass("active");
      if ($(".password-field").attr("type") == "password") {
        $(".password-field").attr("type", "text");
      } else if ($(".password-field").attr("type") == "text") {
        $(".password-field").attr("type", "password");
      }
    });
    $(".show-pass2").on("click", function () {
      $(this).toggleClass("active");
      if ($(".password-field2").attr("type") == "password") {
        $(".password-field2").attr("type", "text");
      } else if ($(".password-field2").attr("type") == "text") {
        $(".password-field2").attr("type", "password");
      }
    });
  };

  /* otp input
  ------------------------------------------------------------------------------------- */
  var otpInput = function () {
    if ($(".digit-group").length > 0) {
      $(".digit-group")
        .find("input")
        .each(function () {
          $(this).attr("maxlength", 1);
          $(this).on("keyup", function (e) {
            var valNum = $(this).val();
            var parent = $($(this).parent());

            if (e.keyCode === 8 || e.keyCode === 37) {
              var prev = parent.find("input#" + $(this).data("previous"));

              if (prev.length) {
                $(prev).select();
              }
            } else if (
              (e.keyCode >= 48 && e.keyCode <= 57) ||
              (e.keyCode >= 65 && e.keyCode <= 90) ||
              (e.keyCode >= 96 && e.keyCode <= 105) ||
              e.keyCode === 39
            ) {
              var next = parent.find("input#" + $(this).data("next"));
              if (!$.isNumeric(valNum)) {
                $(this).val("");
                return false;
              }

              if (next.length) {
                $(next).select();
              } else {
                if (parent.data("autosubmit")) {
                  parent.submit();
                }
              }
            }
          });
        });
    }
  };


  /* back page
  ------------------------------------------------------------------------------------- */
  var backPage = function () {
    $(".back-btn").on("click", function (e) {
      e.stopPropagation();
      e.preventDefault();
      window.history.go(-1);
    });
  };

  /* clear text
  ------------------------------------------------------------------------------------- */
  var clearInput = function () {
    $(".icon-close").on("click", function () {
      $(".clear-ip").val("");
    });
  };
  /* active suggestions
  ------------------------------------------------------------------------------------- */
  var activeSuggest = function () {
    if ($("div").hasClass("action-sheet")) {
      $(".item-check").on("click", function () {
        $(this)
          .parents(".action-sheet")
          .find(".item-check.active")
          .removeClass("active");
        $(this).addClass("active");
      });
    }
    if ($("div").hasClass("modalRight")) {
      $(".item-check-style2").on("click", function () {
        $(this)
          .parents(".modalRight")
          .find(".item-check-style2.active")
          .removeClass("active");
        $(this).addClass("active");
      });
    }

    $(".tag-money,.accent-box-v5,.item-time").click(function () {
      $(".tag-money.active,.accent-box-v5.active,.item-time.active").removeClass("active");
      $(this).addClass("active");
    });
    
    $(".item-category").click(function(){
      $(".item-category.active").removeClass("active");
      $(this).addClass("active");
    });
  };

  /* change value
  ------------------------------------------------------------------------------------- */
  var changeValue = function () {
    $(".tag-money").on("click", function () {
      var val = $(this).text();
      var str = val.replace('R$', '');  // Corrigi para remover 'R$' corretamente
      $(".value_input").val(str);
    });

    $(".dom-value").click(function () {
      $(".dom-text").text($(this).text());
    });

    $(".color-val").click(function () {
      $(".text-val-color").text($(this).text());
    });

    $(".time-val").click(function () {
      $(".text-val-time").text($(this).text());
    });

    $(".market-val").click(function () {
      $(".text-val-market").text($(this).text());
    });

    $(".language-val").click(function () {
      $(".text-val-language").text($(this).text());
    });

    $(".currency-val").click(function () {
      $(".text-val-currency").text($(this).text());
    });
  };

  /* modal bootstrap click handler
  ------------------------------------------------------------------------------------- */
  var clickModalSecond = function () {
    $(".btn-filter-history").click(function () {
      $("#filterHistory").modal("show");
    });
  };

  /* tab slide 
  ------------------------------------------------------------------------------------- */
  var tabSlide = function () {
    if ($(".tab-slide").length > 0) {
      var $1 = $(".tab-slide li.active").width();
      var $2 = $(".tab-slide li.active").position().left;
      $(".nav-item-slide").css({
        width: $1,
        transform: "translateX(" + $2 + "px)",
      });
      $(".tab-slide li").on("click", function () {
        var itemTab = $(this).parent().find("li");
        $(itemTab).removeClass("active");
        $(this).addClass("active");
        var $3 = $(this).width();
        var $4 = $(this).position().left;
        var sideEffect = $(this).parent().find(".item-slide-effect");
        $(sideEffect).css({ width: $3, transform: "translateX(" + $4 + "px)" });
      });
    }
  };
  /* show notification
  ------------------------------------------------------------------------------------- */
  var showNoti = function () {
    if ($("#modalNoti").length > 0) {
      let showPopup = sessionStorage.getItem("showPopup");
      if (!JSON.parse(showPopup)) {
        setTimeout(function () {
          $("#modalNoti").modal('show');
        }, 1000);
      }
    }
  };

   /* hide popup 
  ------------------------------------------------------------------------------------- */
  const hidePopupNoti = function () {
    $(".btn-hide-modal").on("click", function () {
      sessionStorage.setItem("showPopup", true);
    });
  };

  /* preloader 
  ------------------------------------------------------------------------------------- */
  var preloader = function () {
    setTimeout(function () {
      $(".preload").fadeOut("slow", function () {
        $(this).remove();
      });
    }, 500);
  };

  $(function () {
    showPass();
    otpInput();
    // clearItem();
    backPage();
    clearInput();
    activeSuggest();
    changeValue();
    showNoti();
    tabSlide();
    clickModalSecond();
    hidePopupNoti();
    preloader();
  });
})(jQuery);