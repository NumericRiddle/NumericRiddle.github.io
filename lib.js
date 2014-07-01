function defaultprefs() {
	var page = document.getElementById("enclosure");
	var nav = document.getElementById("nav");
	var button = document.getElementById("button");
	page.style.left = "0px";
	nav.style.left = "-250px";
	button.style.left = "0px";
}

function pagecurl(){
	var page = document.getElementById("enclosure");
	var nav = document.getElementById("nav");
	var button = document.getElementById("button");
	if(page.style.left == "250px") {
		page.style.left = "0px";
		button.style.left = "0px";
		nav.style.left = "-250px";
		return;
	}
	if(page.style.left == "0px") {
		page.style.left = "250px";
		button.style.left = "250px";
		nav.style.left = "0px";
		return;
	}
}
function clickreturn() {
	var page = document.getElementById("enclosure");
	var nav = document.getElementById("nav");
	var button = document.getElementById("button");
	page.style.left = "0px";
	nav.style.left = "-250px";
	button.style.left = "0px";
}
