function defaultprefs() {
	var page = document.getElementById("enclosure");
	var nav = document.getElementById("nav");
	var button = document.getElementById("button");
	
	var gone = document.getElementById("group1");
	var gtwo = document.getElementById("group2");
	var gthree = document.getElementById("group3");
	var gfour = document.getElementById("group4");
	var gfive = document.getElementById("group5");
	
	page.style.left = "0px";
	nav.style.left = "-250px";
	button.style.left = "0px";
	
	gone.style.left = "-245px";
	gtwo.style.left = "-245px";
	gthree.style.left = "-245px";
	gfour.style.left = "-245px";
	gfive.style.left = "-245px";
}

function pagecurl(){
	var page = document.getElementById("enclosure");
	var nav = document.getElementById("nav");
	var button = document.getElementById("button");
	
	var gone = document.getElementById("group1");
	var gtwo = document.getElementById("group2");
	var gthree = document.getElementById("group3");
	var gfour = document.getElementById("group4");
	var gfive = document.getElementById("group5");
	if(page.style.left == "250px") {
		page.style.left = "0px";
		button.style.left = "0px";
		nav.style.left = "-250px";
		gone.style.left = "-245px";
		gtwo.style.left = "-245px";
		gthree.style.left = "-245px";
		gfour.style.left = "-245px";
		gfive.style.left = "-245px";
		return;
	}
	if(page.style.left == "0px") {
		page.style.left = "250px";
		button.style.left = "250px";
		nav.style.left = "0px";
		gone.style.left = "0px";
		gtwo.style.left = "0px";
		gthree.style.left = "0px";
		gfour.style.left = "0px";
		gfive.style.left = "0px";
		return;
	}
}
function clickreturn() {
	var page = document.getElementById("enclosure");
	var nav = document.getElementById("nav");
	var button = document.getElementById("button");
	
	var gone = document.getElementById("group1");
	var gtwo = document.getElementById("group2");
	var gthree = document.getElementById("group3");
	var gfour = document.getElementById("group4");
	var gfive = document.getElementById("group5");
	page.style.left = "0px";
	nav.style.left = "-250px";
	button.style.left = "0px";
	gone.style.left = "-245px";
	gtwo.style.left = "-245px";
	gthree.style.left = "-245px";
	gfour.style.left = "-245px";
	gfive.style.left = "-245px";
}
