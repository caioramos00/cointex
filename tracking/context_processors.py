def tracking_context(request):
    vm = getattr(request, "resolver_match", None)
    view_name = None
    if vm and getattr(vm, "view_name", None):
        view_name = vm.view_name
    return {"tracking_view_name": view_name}
